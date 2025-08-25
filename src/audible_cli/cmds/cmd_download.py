import asyncio
import json
import logging
import pathlib
import traceback
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, fields
from datetime import datetime, timezone
from typing import Any

import aiofiles
import click
import httpx
import questionary
from audible import AsyncClient
from audible.exceptions import NotFoundError, RequestError
from click import echo

from ..config import Session
from ..decorators import (
    bunch_size_option,
    end_date_option,
    pass_client,
    pass_session,
    start_date_option,
    timeout_option,
)
from ..downloader import Downloader as NewDownloader
from ..downloader import Status
from ..exceptions import (
    AudibleCliException,
    DirectoryDoesNotExists,
    DownloadUrlExpired,
    NotDownloadableAsAAX,
    VoucherNeedRefresh,
)
from ..models import Library, LibraryItem
from ..utils import Downloader, datetime_type


logger = logging.getLogger(__name__)

CLIENT_HEADERS = {"User-Agent": "Audible/671 CFNetwork/1240.0.4 Darwin/20.6.0"}

COUNTER_LABELS = {
    "aax": "aax files",
    "aaxc": "aaxc files",
    "annotation": "annotations",
    "aycl": "aycl files",
    "aycl_voucher": "aycl voucher files",
    "chapter": "chapters",
    "cover": "covers",
    "pdf": "PDFs",
    "voucher": "voucher files",
    "voucher_saved": "saved voucher files"
}


class DownloadCounter:
    __slots__ = ("_count_data",)
    VALID_COUNTER_NAMES = frozenset(COUNTER_LABELS.keys())

    def __init__(self) -> None:
        self._count_data = {}

    def __getattr__(self, item) -> int:
        if item not in self.VALID_COUNTER_NAMES:
            raise AttributeError(f"Invalid attribute: {item}")
        return self._count_data.get(item, 0)

    def count(self, name: str) -> int:
        new_val = getattr(self, name) + 1
        self._count_data[name] = new_val
        label = COUNTER_LABELS[name]
        logger.debug("Currently downloaded %s: %s", label, new_val)
        return new_val

    def to_dict(self) -> dict:
        return self._count_data

    def has_downloads(self) -> bool:
        return bool(self._count_data)


def display_counter(counter: DownloadCounter) -> None:
    if not counter.has_downloads():
        echo("No new files downloaded.")
        return None

    echo("The download ended with the following result:")

    data = counter.to_dict()
    for k, v in data.items():
        if k == "voucher_saved":
            key_label = "voucher"
        elif k == "voucher":
            unsaved_vouchers = counter.voucher - counter.voucher_saved
            if unsaved_vouchers > 0:
                echo(f"Unsaved voucher: {unsaved_vouchers}")
            continue
        else:
            key_label = COUNTER_LABELS[k]

        echo(f"New {key_label}: {v}")
    return None


@dataclass
class DownloadOptions:
    # Directory settings
    output_dir: pathlib.Path

    # Selection options
    all: bool
    asins: tuple[str, ...]
    titles: tuple[str, ...]

    # Content type options
    aax: bool
    aaxc: bool
    aax_fallback: bool
    annotation: bool
    chapters: bool
    cover: bool
    pdf: bool

    # Quality and formatting options
    quality: str
    cover_sizes: list[str]
    chapter_type: str
    filename_mode: str
    filename_length: int

    # Processing options
    sim_jobs: int
    overwrite_existing: bool
    ignore_errors: bool
    no_confirm: bool

    # Podcast handling
    resolve_podcasts: bool
    ignore_podcasts: bool

    # Date filtering
    start_date: datetime | None = None
    end_date: datetime | None = None

    # Additional options
    bunch_size: int | None = None

    def copy_with(self, **overrides: Any) -> "DownloadOptions":
        """Return a new DownloadOptions instance with the given fields overridden."""
        # Validate override keys
        valid_fields = {f.name for f in fields(self)}
        invalid = [k for k in overrides.keys() if k not in valid_fields]
        if invalid:
            invalid_list = ", ".join(invalid)
            raise TypeError(f"Invalid field(s) for copy_with: {invalid_list}")

        # Build base kwargs from current instance
        kwargs: dict[str, Any] = {}
        for name in valid_fields:
            value = getattr(self, name)
            # Shallow-copy lists to avoid shared mutation
            if isinstance(value, list):
                value = list(value)
            kwargs[name] = value

        # Apply overrides as provided (including explicit None)
        kwargs.update(overrides)

        # Create a new instance (this will run __post_init__ validations)
        return type(self)(**kwargs)

    def __post_init__(self):
        """Validates options after initialization."""
        self._validate_selection_options()
        self._validate_download_options()
        self._validate_podcast_options()
        self._validate_date_options()
        self._handle_aax_fallback()

    def _validate_selection_options(self):
        """Ensure the item selection options are valid."""
        if self.all and any([self.asins, self.titles]):
            raise click.BadOptionUsage(
                "--all",
                "The --all option cannot be used together with --asin or --title options"
            )

    def _validate_download_options(self):
        """Ensure at least one download option is selected."""
        if not any([
            self.aax, self.aax_fallback, self.aaxc,
            self.annotation, self.chapters, self.cover, self.pdf
        ]):
            raise click.BadOptionUsage(
                "download_option",
                "Please select at least one option for what you want to download."
            )

    def _validate_podcast_options(self):
        """Ensure podcast options are not conflicting."""
        if self.resolve_podcasts and self.ignore_podcasts:
            raise click.BadOptionUsage(
                "podcast_option",
                "Do not mix --ignore-podcasts with --resolve-podcasts option."
            )

    def _validate_date_options(self):
        """Ensure date options are valid if provided."""
        if (self.start_date and self.end_date and
                self.start_date > self.end_date):
            raise click.BadOptionUsage(
                "date_option",
                "Start date must be before or equal to the end date"
            )

        if self.start_date is not None:
            logger.info("Selected start date: %s",
                        self.start_date.strftime("%Y-%m-%dT%H:%M:%S.%fZ"))
        if self.end_date is not None:
            logger.info("Selected end date: %s",
                        self.end_date.strftime("%Y-%m-%dT%H:%M:%S.%fZ"))

    def _handle_aax_fallback(self):
        """Handle the  aax_fallback option."""
        if not self.aax_fallback:
            return

        if self.aax:
            logger.info("Using --aax is redundant and can be left when using --aax-fallback")

        # aax_fallback implies aax
        self.aax = False

        if self.aaxc:
            logger.warning("Do not mix --aaxc with the --aax-fallback option.")

        self.aaxc = False


def parse_options(session: Session, options: dict[str, Any]) -> DownloadOptions:
    """Parse CLI options into a structured DownloadOptions object."""
    output_dir = pathlib.Path(options.get("output_dir")).resolve()

    # Resolve chapter_type and filename_mode from config if needed
    chapter_type = options.get("chapter_type")
    if chapter_type == "config":
        chapter_type = session.config.get_profile_option(
            session.selected_profile, "chapter_type", "Tree")

    filename_mode = options.get("filename_mode")
    if filename_mode == "config":
        filename_mode = session.config.get_profile_option(
            session.selected_profile, "filename_mode", "ascii")

    return DownloadOptions(
        # Directory settings
        output_dir=output_dir,

        # Selection options
        all=options.get("all"),
        asins=tuple(options.get("asin")),
        titles=tuple(options.get("title")),

        # Content type options
        aax=options.get("aax"),
        aaxc=options.get("aaxc"),
        aax_fallback=options.get("aax_fallback"),
        annotation=options.get("annotation"),
        chapters=options.get("chapter"),
        cover=options.get("cover"),
        pdf=options.get("pdf"),

        # Quality and formatting options
        quality=options.get("quality"),
        cover_sizes=list(set(options.get("cover_size"))),
        chapter_type=chapter_type,
        filename_mode=filename_mode,
        filename_length=options.get("filename_length"),

        # Processing options
        sim_jobs=options.get("jobs"),
        overwrite_existing=options.get("overwrite"),
        ignore_errors=options.get("ignore_errors"),
        no_confirm=options.get("no_confirm"),

        # Podcast handling
        resolve_podcasts=options.get("resolve_podcasts"),
        ignore_podcasts=options.get("ignore_podcasts"),

        # Date and batch options
        start_date=session.params.get("start_date"),
        end_date=session.params.get("end_date"),
        bunch_size=session.params.get("bunch_size")
    )


async def fetch_library(api_client: AsyncClient, options: DownloadOptions) -> Library:
    # fetch the user library
    library = await Library.from_api_full_sync(
        api_client,
        image_sizes=", ".join(options.cover_sizes),
        bunch_size=options.bunch_size,
        response_groups=(
            "product_desc, media, product_attrs, relationships, "
            "series, customer_rights, pdf_url"
        ),
        start_date=options.start_date,
        end_date=options.end_date,
        status="Active",
    )

    if options.resolve_podcasts:
        await library.resolve_podcasts(start_date=options.start_date, end_date=options.end_date)
        [library.data.remove(i) for i in library if i.is_parent_podcast()]

    return library


def collect_items_by_asin(
        library: Library,
        asins: tuple[str, ...],
        ignore_errors: bool
) -> list[LibraryItem]:
    """Collect library items based on provided ASINs."""
    items = []

    for asin in asins:
        if library.has_asin(asin):
            item = library.get_item_by_asin(asin)
            items.append(item)
        else:
            if not ignore_errors:
                logger.error("Asin %s: Not found in library.", asin)
                raise click.Abort()
            logger.error("Skip asin %s: Not found in library.", asin)

    return items


async def collect_items_by_title(
        library: Library,
        titles: tuple[str, ...],
        no_confirm: bool
) -> list[LibraryItem]:
    """Collect library items based on provided titles."""
    items = []

    for title in titles:
        match = library.search_item_by_title(title)
        full_match = [i for i in match if i[1] == 100]

        if not match:
            logger.error("Skip title %s: Not found in library.", title)
            continue

        if no_confirm:
            # Add all matches automatically if no confirmation needed
            items.extend([i[0] for i in full_match or match])
        else:
            # Ask user to select which items to download
            choices = [
                questionary.Choice(
                    title=f"{i[0].asin} # {i[0].full_title}",
                    value=i[0].asin
                ) for i in full_match or match
            ]

            answer = await questionary.checkbox(
                f"Found the following matches for '{title}'. Which you want to download?",
                choices=choices
            ).unsafe_ask_async()

            if answer:
                items.extend([library.get_item_by_asin(i) for i in answer])

    return items


class SmartQueue:
    def __init__(self, maxsize: int = 0):
        self.queue = asyncio.Queue(maxsize=maxsize)
        self.tasks: set[asyncio.Task] = set()
        self._shutdown = asyncio.Event()
        # Track producer tasks so we don't exit before they finish (Py3.10-safe)
        self._producers: set[asyncio.Task] = set()
        self._producers_done = asyncio.Event()
        self._producers_done.set()  # no producers initially

    @property
    def shutdown_event(self) -> asyncio.Event:
        """Public access to the shutdown event (read-only)."""
        return self._shutdown

    def is_shutdown(self) -> bool:
        """Check if shutdown has been triggered."""
        return self._shutdown.is_set()

    def _track(self, coro: Awaitable, *, name: str | None = None) -> asyncio.Task:
        task = asyncio.create_task(coro, name=name)
        self.tasks.add(task)

        def _done_callback(t: asyncio.Task):
            self.tasks.discard(t)
            try:
                exc = t.exception()
                if exc:
                    logger.error("Task %r failed with: %r", t, exc)
                    # Py3.10-safe traceback logging
                    tb_lines = traceback.format_exception(type(exc), exc, exc.__traceback__)
                    logger.error("".join(tb_lines).rstrip())
                    # Exit on first exception
                    asyncio.create_task(self.shutdown())
            except asyncio.CancelledError:
                pass

        task.add_done_callback(_done_callback)
        return task

    def add_producer(
        self,
        producer_func: Callable[..., Awaitable],
        *args,
        name: str | None = None,
        **kwargs,
    ) -> asyncio.Task:
        # Register producer task and signal not-done
        self._producers_done.clear()
        task = self._track(producer_func(self, *args, **kwargs), name=name)
        self._producers.add(task)

        def _producer_done(_t: asyncio.Task):
            self._producers.discard(_t)
            if not self._producers:
                # No more producers running
                self._producers_done.set()

        task.add_done_callback(_producer_done)
        return task

    def add_consumer(
        self,
        consumer_func: Callable[..., Awaitable],
        *args,
        name: str | None = None,
        **kwargs,
    ) -> asyncio.Task:
        return self._track(consumer_func(self, *args, **kwargs), name=name)

    async def shutdown(self):
        if not self._shutdown.is_set():
            self._shutdown.set()
            for t in list(self.tasks):
                t.cancel()
            await asyncio.gather(*self.tasks, return_exceptions=True)

    # Queue proxies
    async def put(self, item):
        await self.queue.put(item)

    async def get(self):
        return await self.queue.get()

    def task_done(self):
        self.queue.task_done()

    async def join(self):
        await self.queue.join()

    async def wait_until_complete(self):
        """
        Wait until:
          - the queue is drained, and
          - all producers (including dynamically spawned) have finished,
        or until shutdown is triggered (on first exception).
        """
        while not self.is_shutdown():
            # Wait for the queue to drain or shutdown
            join_task = asyncio.create_task(self.join())
            shutdown_task = asyncio.create_task(self._shutdown.wait())
            done, pending = await asyncio.wait(
                {join_task, shutdown_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for t in pending:
                t.cancel()
                await asyncio.gather(t, return_exceptions=True)
            if self.is_shutdown():
                return

            # Wait for all producers to complete or shutdown
            prod_done_task = asyncio.create_task(self._producers_done.wait())
            shutdown_task = asyncio.create_task(self._shutdown.wait())
            done, pending = await asyncio.wait(
                {prod_done_task, shutdown_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for t in pending:
                t.cancel()
                await asyncio.gather(t, return_exceptions=True)
            if self.is_shutdown():
                return

            # If queue still empty and no producers left, we're done
            if self.queue.empty() and not self._producers:
                return


@dataclass
class DownloadJob:
    item: LibraryItem
    options: DownloadOptions
    client: httpx.AsyncClient
    queue: SmartQueue
    counter: DownloadCounter

    def create_base_filename(self) -> str:
        """Create a base filename for the item."""
        return self.item.create_base_filename(
            self.options.filename_mode, self.options.filename_length
        )


async def create_download_jobs(
    items: list[LibraryItem],
    options: DownloadOptions,
    client: httpx.AsyncClient,
    queue: SmartQueue,
    counter: DownloadCounter,
) -> list[DownloadJob]:
    """Process library items and prepare them for download.

    Handles parent podcasts by fetching their child items and creating appropriate
    directories for podcast content.
    """
    processed_items = []

    # Process all items in the list
    for item in items:
        # Skip already processed items
        if item.asin in [i.item.asin for i in processed_items]:
            continue

        # Handle parent podcasts
        if item.is_parent_podcast():
            if options.ignore_podcasts:
                continue

            # Fetch child items if needed
            if item._children is None:
                await item.get_child_items(
                    start_date=options.start_date,
                    end_date=options.end_date
                )

            # Create dedicated directory for podcast
            podcast_dir = item.create_base_filename(options.filename_mode)
            output_dir = options.output_dir / podcast_dir
            if not output_dir.is_dir():
                output_dir.mkdir(parents=True)

            # Set up custom options for podcast children
            options_for_children = options.copy_with(output_dir=output_dir)

            # Add child items to processing queue with custom output directory
            for child_item in item._children:
                # Add to the process queue if not already included
                if child_item.asin not in [i.item.asin for i in processed_items]:
                    download_job = DownloadJob(child_item, options_for_children, client, queue, counter)
                    processed_items.append(download_job)
        else:
            download_job = DownloadJob(item, options, client, queue, counter)
            processed_items.append(download_job)

    return processed_items


async def download_covers(job: DownloadJob) -> None:
    base_filename = job.create_base_filename()

    for cover_size in job.options.cover_sizes:
        filename = f"{base_filename}_({cover_size!s}).jpg"
        filepath = job.options.output_dir / filename

        url = job.item.get_cover_url(cover_size)
        if url is None:
            logger.error(
                "Cover size %s notfound for %s}", cover_size, job.item.full_title
            )
            return None

        dl = Downloader(url, filepath, job.client, job.options.overwrite_existing, "image/jpeg")
        downloaded = await dl.run(stream=False, pb=False)
        if downloaded:
            job.counter.count("cover")
    return None


async def download_pdf(job: DownloadJob) -> None:
    url = job.item.get_pdf_url()
    if url is None:
        logger.info("No PDF found for %s", job.item.full_title)
        return None

    base_filename = job.create_base_filename()
    filename = base_filename + ".pdf"
    filepath = job.options.output_dir / filename
    dl = Downloader(
        url, filepath, job.client, job.options.overwrite_existing,
        ["application/octet-stream", "application/pdf"]
    )
    downloaded = await dl.run(stream=False, pb=False)

    if downloaded:
        job.counter.count("pdf")
    return None


async def download_chapters(job: DownloadJob) -> None:
    options = job.options
    if not options.output_dir.is_dir():
        raise DirectoryDoesNotExists(options.output_dir)

    base_filename = job.create_base_filename()
    filename = base_filename + "-chapters.json"
    file = options.output_dir / filename
    if file.exists() and not options.overwrite_existing:
        logger.info("File %s already exists. Skip saving chapters", file)
        return None

    try:
        metadata = await job.item.get_content_metadata(job.options.quality, chapter_type=options.chapter_type)
    except NotFoundError:
        logger.info("No chapters found for %s", job.item.full_title)
        return None

    metadata = json.dumps(metadata, indent=4)
    async with aiofiles.open(file, "w") as f:
        await f.write(metadata)
    logger.info("Chapter file saved in style '%s' to %s.", options.chapter_type.upper(), file)
    job.counter.count("chapter")
    return None


async def download_annotations(job: DownloadJob) -> None:
    options = job.options
    if not options.output_dir.is_dir():
        raise DirectoryDoesNotExists(options.output_dir)

    base_filename = job.create_base_filename()
    filename = base_filename + "-annotations.json"
    file = options.output_dir / filename
    if file.exists() and not options.overwrite_existing:
        logger.info("File %s already exists. Skip saving annotations", file)
        return None

    try:
        annotation = await job.item.get_annotations()
    except NotFoundError:
        logger.info("No annotations found for %s.", job.item.full_title)
        return None
    except RequestError:
        logger.error("Failed to get annotations for %s.", job.item.full_title)
        return None

    annotation = json.dumps(annotation, indent=4)
    async with aiofiles.open(file, "w") as f:
        await f.write(annotation)
    logger.info("Annotation file saved to %s.", file)
    job.counter.count("annotation")
    return None


async def _get_audioparts(job: DownloadJob) -> list[LibraryItem]:
    parts = []
    child_library: Library = await job.item.get_child_items()
    if child_library is not None:
        for child in child_library:
            if (
                child.content_delivery_type is not None
                and child.content_delivery_type == "AudioPart"
            ):
                parts.append(child)

    return parts


async def _add_audioparts_to_queue(queue: SmartQueue, job: DownloadJob, download_mode: str) -> None:
    parts = await _get_audioparts(job)

    for part in parts:
        logger.info("Item %s has audio parts. Adding parts to queue.", part.full_title)

        if download_mode == "aax":
            options = job.options.copy_with(
                aax=True,
                aax_fallback=False,
                aaxc=False,
                annotation=False,
                chapters=False,
                cover=False,
                pdf=False
            )
        else:
            options = job.options.copy_with(
                aax=False,
                aax_fallback=False,
                aaxc=True,
                annotation=False,
                chapters=False,
                cover=False,
                pdf=False
            )

        part_job = DownloadJob(
            item=part,
            options=options,
            client=job.client,
            queue=queue,
            counter=job.counter
        )

        queue.add_producer(produce_jobs, [part_job])



async def download_aax(job: DownloadJob, retry: int = 0) -> None:
    # url, codec = await item.get_aax_url(quality)
    options = job.options
    try:
        url, codec = await job.item.get_aax_url_old(options.quality)
    except NotDownloadableAsAAX:
        if options.aax_fallback:
            logger.info("Fallback to aaxc for %s", job.item.full_title)
            await job.queue.put((download_aax, job))
            return None
        raise
    except httpx.RemoteProtocolError:
        if retry == 3:
            logger.error("Failed to get AAX URL for %s, abort", job.item.full_title)
            return None
        else:
            logger.warning("Failed to get AAX URL for %s, retrying", job.item.full_title)
            await asyncio.sleep(5)
            next_retry = retry + 1
            await job.queue.put((download_aax, job, next_retry))
            await asyncio.sleep(1)
            return None


    base_filename = job.create_base_filename()
    filename = base_filename + f"-{codec}.aax"
    filepath = options.output_dir / filename

    dl = NewDownloader(
        source=url,
        client=job.client,
        expected_types=[
            "audio/aax", "audio/vnd.audible.aax", "audio/audible"
        ]
    )
    downloaded = await dl.run(target=filepath, force_reload=options.overwrite_existing)

    if downloaded.status == Status.Success:
        job.counter.count("aax")
    elif downloaded.status == Status.DownloadIndividualParts:
        logger.info("Item %s must be downloaded in parts. Adding parts to queue", filepath)
        # Ensure new producers are tracked by SmartQueue (Py3.10-safe)
        job.queue.add_producer(_add_audioparts_to_queue, job, download_mode="aax")
        await asyncio.sleep(1)
    return None


async def _reuse_voucher(lr_file, job: DownloadJob) -> tuple[dict, httpx.URL, str]:
    logger.info("Loading data from voucher file %s.", lr_file)
    async with aiofiles.open(lr_file) as f:
        lr = await f.read()
    lr = json.loads(lr)
    content_license = lr["content_license"]

    if not content_license["status_code"] == "Granted":
        raise AudibleCliException("License not granted")

    # try to get the user id
    user_id = None
    if job.item._client is not None:
        auth = job.item._client.auth
        if auth.customer_info is not None:
            user_id = auth.customer_info.get("user_id")

    # Verification of allowed user
    if user_id is None:
        logger.debug("No user id found. Skip user verification.")
    elif "allowed_users" in content_license:
        allowed_users = content_license["allowed_users"]
        if allowed_users and user_id not in allowed_users:
            # Don't proceed here to prevent an overwriting voucher file
            msg = f"The current user is not entitled to use the voucher {lr_file}."
            raise AudibleCliException(msg)
    else:
        logger.debug("%s does not contain allowed users key.", lr_file)

    # Verification of voucher validity
    if "refresh_date" in content_license:
        refresh_date = content_license["refresh_date"]
        refresh_date = datetime_type.convert(refresh_date, None, None)
        if refresh_date < datetime.now(timezone.utc):
            raise VoucherNeedRefresh(lr_file)

    content_metadata = content_license["content_metadata"]
    url = httpx.URL(content_metadata["content_url"]["offline_url"])
    codec = content_metadata["content_reference"]["content_format"]

    expires = url.params.get("Expires")
    if expires:
        expires = datetime.fromtimestamp(int(expires), timezone.utc)
        now = datetime.now(timezone.utc)
        if expires < now:
            raise DownloadUrlExpired(lr_file)

    return lr, url, codec


async def download_aaxc(job: DownloadJob) -> None:
    lr, url, codec = None, None, None
    options = job.options
    base_filename = job.create_base_filename()

    # https://github.com/mkb79/audible-cli/issues/60
    if not options.overwrite_existing:
        codec, _ = job.item._get_codec(options.quality)
        if codec is not None:
            filepath = pathlib.Path(
                options.output_dir) / f"{base_filename}-{codec}.aaxc"
            lr_file = filepath.with_suffix(".voucher")

            if lr_file.is_file():
                if filepath.is_file():
                    logger.info("File %s already exists. Skip download.", lr_file)
                    logger.info("File %s already exists. Skip download.", filepath)
                    return None

                try:
                    lr, url, codec = await _reuse_voucher(lr_file, job)
                except DownloadUrlExpired:
                    logger.debug("Download url in %s is expired. Refreshing license.", lr_file)
                except VoucherNeedRefresh:
                    logger.debug("Refresh date for voucher %s reached. Refreshing license.", lr_file)

    is_aycl = job.item.benefit_id == "AYCL"

    new_license = False
    if lr is None or url is None or codec is None:
        url, codec, lr = await job.item.get_aaxc_url(options.quality)
        new_license = True
        job.counter.count("voucher")
        if is_aycl:
            job.counter.count("aycl_voucher")

    if codec.lower() == "mpeg":
        ext = "mp3"
    else:
        ext = "aaxc"

    filepath = pathlib.Path(
        options.output_dir) / f"{base_filename}-{codec}.{ext}"
    lr_file = filepath.with_suffix(".voucher")

    if lr_file.is_file() and not new_license:
        logger.info("File %s already exists. Skip download.", lr_file)
    else:
        lr = json.dumps(lr, indent=4)
        async with aiofiles.open(lr_file, "w") as f:
            await f.write(lr)
        logger.info("Voucher file saved to %s.", lr_file)
        job.counter.count("voucher_saved")

    dl = NewDownloader(
        source=url,
        client=job.client,
        expected_types=[
            "audio/aax", "audio/vnd.audible.aax", "audio/mpeg", "audio/x-m4a",
            "audio/audible"
        ],
    )
    downloaded = await dl.run(target=filepath, force_reload=options.overwrite_existing)

    if downloaded.status == Status.Success:
        job.counter.count("aaxc")
        if is_aycl:
            job.counter.count("aycl")
    elif downloaded.status == Status.DownloadIndividualParts:
        logger.info("Item %s must be downloaded in parts. Adding parts to queue", filepath)
        job.queue.add_producer(_add_audioparts_to_queue, job, download_mode="aaxc")
        await asyncio.sleep(1)
    return None


async def produce_jobs(queue: SmartQueue, jobs: list[DownloadJob]) -> None:
    """Add a download job to the queue with appropriate options."""
    try:
        for job in jobs:
            if job.options.cover:
                cmd = download_covers
                logger.debug("Adding cover download job for %s", job.item.full_title)
                await job.queue.put((cmd, job))

            if job.options.pdf:
                cmd = download_pdf
                logger.debug("Adding PDF download job for %s", job.item.full_title)
                await job.queue.put((cmd, job))

            if job.options.chapters:
                cmd = download_chapters
                logger.debug("Adding chapters download job for %s", job.item.full_title)
                await job.queue.put((cmd, job))

            if job.options.annotation:
                cmd = download_annotations
                logger.debug("Adding annotations download job for %s", job.item.full_title)
                await job.queue.put((cmd, job))

            if job.options.aax or job.options.aax_fallback:
                logger.debug("Adding AAX download job for %s", job.item.full_title)
                cmd = download_aax
                await job.queue.put((cmd, job))

            if job.options.aaxc:
                logger.debug("Adding AAXC download job for %s", job.item.full_title)
                cmd = download_aaxc
                await job.queue.put((cmd, job))
    except asyncio.CancelledError:
        raise


async def consume_jobs(queue: SmartQueue, name: str) -> None:
    job = None
    try:
        while not queue.is_shutdown():
            cmd, job, *args = await queue.get()
            await cmd(job, *args)
            queue.task_done()
    except asyncio.CancelledError:
        raise
    except Exception as e:
        if job and not job.options.ignore_errors:
            raise
        else:
            logger.error(e)


@click.command("download")
@click.option(
    "--output-dir", "-o",
    type=click.Path(exists=True, dir_okay=True),
    default=pathlib.Path().cwd(),
    help="Directory where downloaded files will be saved (defaults to current working directory)"
)
@click.option(
    "--all",
    is_flag=True,
    help="Download all books from your library (overrides the --asin and --title options)"
)
@click.option(
    "--asin", "-a",
    multiple=True,
    help="ASIN(s) of the audiobook(s) to download (can be specified multiple times)"
)
@click.option(
    "--title", "-t",
    multiple=True,
    help="tile of the audiobook (partial search)"
)
@click.option(
    "--aax",
    is_flag=True,
    help="Download book in aax format"
)
@click.option(
    "--aaxc",
    is_flag=True,
    help="Download book in aaxc format incl. voucher file"
)
@click.option(
    "--aax-fallback",
    is_flag=True,
    help="Download book in aax format and fallback to aaxc, if former is not supported."
)
@click.option(
    "--quality", "-q",
    default="best",
    show_default=True,
    type=click.Choice(["best", "high", "normal"]),
    help="download quality"
)
@click.option(
    "--pdf",
    is_flag=True,
    help="downloads the pdf in addition to the audiobook"
)
@click.option(
    "--cover",
    is_flag=True,
    help="downloads the cover in addition to the audiobook"
)
@click.option(
    "--cover-size",
    type=click.Choice(["252", "315", "360", "408", "500", "558", "570", "882",
                       "900", "1215"]),
    default=["500"],
    multiple=True,
    help="The cover pixel size. This option can be provided multiple times."
)
@click.option(
    "--chapter",
    is_flag=True,
    help="Saves chapter metadata as JSON file."
)
@click.option(
    "--chapter-type",
    default="config",
    type=click.Choice(["Flat", "Tree", "config"], case_sensitive=False),
    help="The chapter type."
)
@click.option(
    "--annotation",
    is_flag=True,
    help="saves the annotations (e.g. bookmarks, notes) as JSON file"
)
@start_date_option
@end_date_option
@click.option(
    "--no-confirm", "-y",
    is_flag=True,
    help="start without confirm"
)
@click.option(
    "--overwrite",
    is_flag=True,
    help="rename existing files"
)
@click.option(
    "--ignore-errors",
    is_flag=True,
    help="ignore errors and continue with the rest"
)
@click.option(
    "--jobs", "-j",
    type=int,
    default=3,
    show_default=True,
    help="number of simultaneous downloads"
)
@click.option(
    "--filename-mode", "-f",
    type=click.Choice(
        ["config", "ascii", "asin_ascii", "unicode", "asin_unicode", "asin_only"]
    ),
    default="config",
    help="Filename mode to use. [default: config]"
)
@click.option(
    "--filename-length",
    "-l",
    default=230,
    show_default=True,
    help="Maximum filename length.",
)
@timeout_option
@click.option(
    "--resolve-podcasts",
    is_flag=True,
    help="Resolve podcasts to download a single episode via asin or title"
)
@click.option(
    "--ignore-podcasts",
    is_flag=True,
    help="Ignore a podcast if it have episodes"
)
@bunch_size_option
@pass_session
@pass_client(headers=CLIENT_HEADERS)
async def cli(session: Session, api_client: AsyncClient, **params: Any):
    """Download audiobook(s) from an Audible library."""
    options = parse_options(session, params)
    library = await fetch_library(api_client, options)

    # collect items to download
    items = []

    if options.all:
        items = list(library)
    else:
        # Collect items by ASIN
        asin_items = collect_items_by_asin(library, options.asins, options.ignore_errors)
        items.extend(asin_items)

        # Collect items by title
        title_items = await collect_items_by_title(library, options.titles, options.no_confirm)
        items.extend(title_items)

    queue = SmartQueue(options.sim_jobs)
    counter = DownloadCounter()

    download_jobs = await create_download_jobs(items, options, api_client.session, queue,
                                               counter)

    for i in range(options.sim_jobs):
        name = f"consumer-{i}"
        queue.add_consumer(consume_jobs, name)

    queue.add_producer(produce_jobs, download_jobs, name="producer-1")

    try:
        # Wait until all work completes OR shutdown occurs (on the first exception)
        done, pending = await asyncio.wait(
            {
                asyncio.create_task(queue.wait_until_complete()),
                asyncio.create_task(queue.shutdown_event.wait()),
            },
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    finally:
        await queue.shutdown()
        display_counter(counter)

