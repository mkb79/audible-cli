import asyncio
import asyncio.log
import asyncio.sslproto
import json
import pathlib
import logging
from datetime import datetime

import aiofiles
import click
import httpx
import questionary
from audible.exceptions import NotFoundError
from click import echo

from ..decorators import (
    bunch_size_option,
    end_date_option,
    start_date_option,
    timeout_option,
    pass_client,
    pass_session
)
from ..exceptions import (
    AudibleCliException,
    DirectoryDoesNotExists,
    DownloadUrlExpired,
    NotDownloadableAsAAX,
    VoucherNeedRefresh
)
from ..models import Library
from ..utils import datetime_type, Downloader


logger = logging.getLogger("audible_cli.cmds.cmd_download")

CLIENT_HEADERS = {
    "User-Agent": "Audible/671 CFNetwork/1240.0.4 Darwin/20.6.0"
}


class DownloadCounter:
    def __init__(self):
        self._aax: int = 0
        self._aaxc: int = 0
        self._annotation: int = 0
        self._chapter: int = 0
        self._cover: int = 0
        self._pdf: int = 0
        self._voucher: int = 0
        self._voucher_saved: int = 0
        self._aycl = 0
        self._aycl_voucher = 0

    @property
    def aax(self):
        return self._aax

    def count_aax(self):
        self._aax += 1
        logger.debug(f"Currently downloaded aax files: {self.aax}")

    @property
    def aaxc(self):
        return self._aaxc

    def count_aaxc(self):
        self._aaxc += 1
        logger.debug(f"Currently downloaded aaxc files: {self.aaxc}")

    @property
    def aycl(self):
        return self._aycl

    def count_aycl(self):
        self._aycl += 1
        # log as error to display this message in any cases
        logger.debug(f"Currently downloaded aycl files: {self.aycl}")

    @property
    def aycl_voucher(self):
        return self._aycl_voucher

    def count_aycl_voucher(self):
        self._aycl_voucher += 1
        # log as error to display this message in any cases
        logger.debug(f"Currently downloaded aycl voucher files: {self.aycl_voucher}")

    @property
    def annotation(self):
        return self._annotation

    def count_annotation(self):
        self._annotation += 1
        logger.debug(f"Currently downloaded annotations: {self.annotation}")

    @property
    def chapter(self):
        return self._chapter

    def count_chapter(self):
        self._chapter += 1
        logger.debug(f"Currently downloaded chapters: {self.chapter}")

    @property
    def cover(self):
        return self._cover

    def count_cover(self):
        self._cover += 1
        logger.debug(f"Currently downloaded covers: {self.cover}")

    @property
    def pdf(self):
        return self._pdf

    def count_pdf(self):
        self._pdf += 1
        logger.debug(f"Currently downloaded PDFs: {self.pdf}")

    @property
    def voucher(self):
        return self._voucher

    def count_voucher(self):
        self._voucher += 1
        logger.debug(f"Currently downloaded voucher files: {self.voucher}")

    @property
    def voucher_saved(self):
        return self._voucher_saved

    def count_voucher_saved(self):
        self._voucher_saved += 1
        logger.debug(f"Currently saved voucher files: {self.voucher_saved}")

    def as_dict(self) -> dict:
        return {
            "aax": self.aax,
            "aaxc": self.aaxc,
            "annotation": self.annotation,
            "chapter": self.chapter,
            "cover": self.cover,
            "pdf": self.pdf,
            "voucher": self.voucher,
            "voucher_saved": self.voucher_saved,
            "aycl": self.aycl,
            "aycl_voucher": self.aycl_voucher
        }

    def has_downloads(self):
        for _, v in self.as_dict().items():
            if v > 0:
                return True

        return False


counter = DownloadCounter()


async def download_cover(
        client, output_dir, base_filename, item, res, overwrite_existing
):
    filename = f"{base_filename}_({str(res)}).jpg"
    filepath = output_dir / filename

    url = item.get_cover_url(res)
    if url is None:
        logger.error(
            f"No COVER with size {res} found for {item.full_title}"
        )
        return

    dl = Downloader(url, filepath, client, overwrite_existing, "image/jpeg")
    downloaded = await dl.run(stream=False, pb=False)

    if downloaded:
        counter.count_cover()


async def download_pdf(
        client, output_dir, base_filename, item, overwrite_existing
):
    url = item.get_pdf_url()
    if url is None:
        logger.info(f"No PDF found for {item.full_title}")
        return

    filename = base_filename + ".pdf"
    filepath = output_dir / filename
    dl = Downloader(
        url, filepath, client, overwrite_existing,
        ["application/octet-stream", "application/pdf"]
    )
    downloaded = await dl.run(stream=False, pb=False)

    if downloaded:
        counter.count_pdf()


async def download_chapters(
        output_dir, base_filename, item, quality, overwrite_existing
):
    if not output_dir.is_dir():
        raise DirectoryDoesNotExists(output_dir)

    filename = base_filename + "-chapters.json"
    file = output_dir / filename
    if file.exists() and not overwrite_existing:
        logger.info(
            f"File {file} already exists. Skip saving chapters"
        )
        return True

    try:
        metadata = await item.get_content_metadata(quality)
    except NotFoundError:
        logger.info(
            f"No chapters found for {item.full_title}."
        )
        return
    metadata = json.dumps(metadata, indent=4)
    async with aiofiles.open(file, "w") as f:
        await f.write(metadata)
    logger.info(f"Chapter file saved to {file}.")
    counter.count_chapter()


async def download_annotations(
        output_dir, base_filename, item, overwrite_existing
):
    if not output_dir.is_dir():
        raise DirectoryDoesNotExists(output_dir)

    filename = base_filename + "-annotations.json"
    file = output_dir / filename
    if file.exists() and not overwrite_existing:
        logger.info(
            f"File {file} already exists. Skip saving annotations"
        )
        return True

    try:
        annotation = await item.get_annotations()
    except NotFoundError:
        logger.info(
            f"No annotations found for {item.full_title}."
        )
        return
    annotation = json.dumps(annotation, indent=4)
    async with aiofiles.open(file, "w") as f:
        await f.write(annotation)
    logger.info(f"Annotation file saved to {file}.")
    counter.count_annotation()


async def download_aax(
        client, output_dir, base_filename, item, quality, overwrite_existing,
        aax_fallback
):
    # url, codec = await item.get_aax_url(quality)
    try:
        url, codec = await item.get_aax_url_old(quality)
    except NotDownloadableAsAAX:
        if aax_fallback:
            logger.info(f"Fallback to aaxc for {item.full_title}")
            return await download_aaxc(
                client=client,
                output_dir=output_dir,
                base_filename=base_filename,
                item=item,
                quality=quality,
                overwrite_existing=overwrite_existing
            )
        raise

    filename = base_filename + f"-{codec}.aax"
    filepath = output_dir / filename
    dl = Downloader(
        url, filepath, client, overwrite_existing,
        ["audio/aax", "audio/vnd.audible.aax", "audio/audible"]
    )
    downloaded = await dl.run(pb=True)

    if downloaded:
        counter.count_aax()


async def _reuse_voucher(lr_file, item):
    logger.info(f"Loading data from voucher file {lr_file}.")
    async with aiofiles.open(lr_file, "r") as f:
        lr = await f.read()
    lr = json.loads(lr)
    content_license = lr["content_license"]

    assert content_license["status_code"] == "Granted", "License not granted"

    # try to get the user id
    user_id = None
    if item._client is not None:
        auth = item._client.auth
        if auth.customer_info is not None:
            user_id = auth.customer_info.get("user_id")

    # Verification of allowed user
    if user_id is None:
        logger.debug("No user id found. Skip user verification.")
    else:
        if "allowed_users" in content_license:
            allowed_users = content_license["allowed_users"]
            if allowed_users and user_id not in allowed_users:
                # Don't proceed here to prevent overwriting voucher file
                msg = f"The current user is not entitled to use the voucher {lr_file}."
                raise AudibleCliException(msg)
        else:
            logger.debug(f"{lr_file} does not contain allowed users key.")

    # Verification of voucher validity
    if "refresh_date" in content_license:
        refresh_date = content_license["refresh_date"]
        refresh_date = datetime_type.convert(refresh_date, None, None)
        if refresh_date < datetime.utcnow():
            raise VoucherNeedRefresh(lr_file)

    content_metadata = content_license["content_metadata"]
    url = httpx.URL(content_metadata["content_url"]["offline_url"])
    codec = content_metadata["content_reference"]["content_format"]

    expires = url.params.get("Expires")
    if expires:
        expires = datetime.utcfromtimestamp(int(expires))
        now = datetime.utcnow()
        if expires < now:
            raise DownloadUrlExpired(lr_file)

    return lr, url, codec


async def download_aaxc(
        client, output_dir, base_filename, item,
        quality, overwrite_existing
):
    lr, url, codec = None, None, None

    # https://github.com/mkb79/audible-cli/issues/60
    if not overwrite_existing:
        codec, _ = item._get_codec(quality)
        if codec is not None:
            filepath = pathlib.Path(
                output_dir) / f"{base_filename}-{codec}.aaxc"
            lr_file = filepath.with_suffix(".voucher")

            if lr_file.is_file():
                if filepath.is_file():
                    logger.info(
                        f"File {lr_file} already exists. Skip download."
                    )
                    logger.info(
                        f"File {filepath} already exists. Skip download."
                    )
                    return

                try:
                    lr, url, codec = await _reuse_voucher(lr_file, item)
                except DownloadUrlExpired:
                    logger.debug(f"Download url in {lr_file} is expired. Refreshing license.")
                    overwrite_existing = True
                except VoucherNeedRefresh:
                    logger.debug(f"Refresh date for voucher {lr_file} reached. Refreshing license.")
                    overwrite_existing = True

    is_aycl = item.benefit_id == "AYCL"

    if lr is None or url is None or codec is None:
        url, codec, lr = await item.get_aaxc_url(quality)
        counter.count_voucher()
        if is_aycl:
            counter.count_aycl_voucher()

    if codec.lower() == "mpeg":
        ext = "mp3"
    else:
        ext = "aaxc"

    filepath = pathlib.Path(
        output_dir) / f"{base_filename}-{codec}.{ext}"
    lr_file = filepath.with_suffix(".voucher")

    if lr_file.is_file() and not overwrite_existing:
        logger.info(
            f"File {lr_file} already exists. Skip download."
        )
    else:
        lr = json.dumps(lr, indent=4)
        async with aiofiles.open(lr_file, "w") as f:
            await f.write(lr)
        logger.info(f"Voucher file saved to {lr_file}.")
        counter.count_voucher_saved()

    dl = Downloader(
        url,
        filepath,
        client,
        overwrite_existing,
        [
            "audio/aax", "audio/vnd.audible.aax", "audio/mpeg", "audio/x-m4a",
            "audio/audible"
        ]
    )
    downloaded = await dl.run(pb=True)

    if downloaded:
        counter.count_aaxc()
        if is_aycl:
            counter.count_aycl()


async def consume(queue, ignore_errors):
    while True:
        cmd, kwargs = await queue.get()
        try:
            await cmd(**kwargs)
        except Exception as e:
            logger.error(e)
            if not ignore_errors:
                raise
        finally:
            queue.task_done()


def queue_job(
        queue,
        get_cover,
        get_pdf,
        get_annotation,
        get_chapters,
        get_aax,
        get_aaxc,
        client,
        output_dir,
        filename_mode,
        item,
        cover_sizes,
        quality,
        overwrite_existing,
        aax_fallback
):
    base_filename = item.create_base_filename(filename_mode)

    if get_cover:
        for cover_size in cover_sizes:
            cmd = download_cover
            kwargs = {
                "client": client,
                "output_dir": output_dir,
                "base_filename": base_filename,
                "item": item,
                "res": cover_size,
                "overwrite_existing": overwrite_existing
            }
            queue.put_nowait((cmd, kwargs))

    if get_pdf:
        cmd = download_pdf
        kwargs = {
            "client": client,
            "output_dir": output_dir,
            "base_filename": base_filename,
            "item": item,
            "overwrite_existing": overwrite_existing
        }
        queue.put_nowait((cmd, kwargs))

    if get_chapters:
        cmd = download_chapters
        kwargs = {
            "output_dir": output_dir,
            "base_filename": base_filename,
            "item": item,
            "quality": quality,
            "overwrite_existing": overwrite_existing
        }
        queue.put_nowait((cmd, kwargs))

    if get_annotation:
        cmd = download_annotations
        kwargs = {
            "output_dir": output_dir,
            "base_filename": base_filename,
            "item": item,
            "overwrite_existing": overwrite_existing
        }
        queue.put_nowait((cmd, kwargs))

    if get_aax:
        cmd = download_aax
        kwargs = {
            "client": client,
            "output_dir": output_dir,
            "base_filename": base_filename,
            "item": item,
            "quality": quality,
            "overwrite_existing": overwrite_existing,
            "aax_fallback": aax_fallback
        }
        queue.put_nowait((cmd, kwargs))

    if get_aaxc:
        cmd = download_aaxc
        kwargs = {
            "client": client,
            "output_dir": output_dir,
            "base_filename": base_filename,
            "item": item,
            "quality": quality,
            "overwrite_existing": overwrite_existing
        }
        queue.put_nowait((cmd, kwargs))


def display_counter():
    if counter.has_downloads():
        echo("The download ended with the following result:")
        for k, v in counter.as_dict().items():
            if v == 0:
                continue

            if k == "voucher_saved":
                k = "voucher"
            elif k == "aycl_voucher":
                k = "aycl voucher"
            elif k == "voucher":
                diff = v - counter.voucher_saved
                if diff > 0:
                    echo(f"Unsaved voucher: {diff}")
                continue
            echo(f"New {k} files: {v}")
    else:
        echo("No new files downloaded.")


@click.command("download")
@click.option(
    "--output-dir", "-o",
    type=click.Path(exists=True, dir_okay=True),
    default=pathlib.Path().cwd(),
    help="output dir, uses current working dir as default"
)
@click.option(
    "--all",
    is_flag=True,
    help="download all library items, overrides --asin and --title options"
)
@click.option(
    "--asin", "-a",
    multiple=True,
    help="asin of the audiobook"
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
    help="saves chapter metadata as JSON file"
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
        ["config", "ascii", "asin_ascii", "unicode", "asin_unicode"]
    ),
    default="config",
    help="Filename mode to use. [default: config]"
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
async def cli(session, api_client, **params):
    """download audiobook(s) from library"""
    client = api_client.session
    output_dir = pathlib.Path(params.get("output_dir")).resolve()

    # which item(s) to download
    get_all = params.get("all") is True
    asins = params.get("asin")
    titles = params.get("title")
    if get_all and (asins or titles):
        logger.error(f"Do not mix *asin* or *title* option with *all* option.")
        click.Abort()

    # what to download
    get_aax = params.get("aax")
    get_aaxc = params.get("aaxc")
    aax_fallback = params.get("aax_fallback")
    if aax_fallback:
        if get_aax:
            logger.info(
                "Using --aax is redundant and can be left when using --aax-fallback"
            )
        get_aax = True
        if get_aaxc:
            logger.warning("Do not mix --aaxc with --aax-fallback option.")
    get_annotation = params.get("annotation")
    get_chapters = params.get("chapter")
    get_cover = params.get("cover")
    get_pdf = params.get("pdf")
    if not any(
        [get_aax, get_aaxc, get_annotation, get_chapters, get_cover, get_pdf]
    ):
        logger.error("Please select an option what you want download.")
        raise click.Abort()

    # additional options
    sim_jobs = params.get("jobs")
    quality = params.get("quality")
    cover_sizes = list(set(params.get("cover_size")))
    overwrite_existing = params.get("overwrite")
    ignore_errors = params.get("ignore_errors")
    no_confirm = params.get("no_confirm")
    resolve_podcats = params.get("resolve_podcasts")
    ignore_podcasts = params.get("ignore_podcasts")
    bunch_size = session.params.get("bunch_size")

    start_date = session.params.get("start_date")
    end_date = session.params.get("end_date")
    if all([start_date, end_date]) and start_date > end_date:
        logger.error("start date must be before or equal the end date")
        raise click.Abort()

    if start_date is not None:
        logger.info(
            f"Selected start date: {start_date.strftime('%Y-%m-%dT%H:%M:%S.%fZ')}"
        )
    if end_date is not None:
        logger.info(
            f"Selected end date: {end_date.strftime('%Y-%m-%dT%H:%M:%S.%fZ')}"
        )

    filename_mode = params.get("filename_mode")
    if filename_mode == "config":
        filename_mode = session.config.get_profile_option(
            session.selected_profile, "filename_mode") or "ascii"

    # fetch the user library
    library = await Library.from_api_full_sync(
        api_client,
        image_sizes=", ".join(cover_sizes),
        bunch_size=bunch_size,
        response_groups=(
            "product_desc, media, product_attrs, relationships, "
            "series, customer_rights, pdf_url"
        ),
        start_date=start_date,
        end_date=end_date,
        status="Active",
    )

    if resolve_podcats:
        await library.resolve_podcats(start_date=start_date, end_date=end_date)

    # collect jobs
    jobs = []

    if get_all:
        asins = []
        titles = []
        for i in library:
            jobs.append(i.asin)

    for asin in asins:
        if library.has_asin(asin):
            jobs.append(asin)
        else:
            if not ignore_errors:
                logger.error(f"Asin {asin} not found in library.")
                click.Abort()
            logger.error(
                f"Skip asin {asin}: Not found in library"
            )

    for title in titles:
        match = library.search_item_by_title(title)
        full_match = [i for i in match if i[1] == 100]

        if match:
            if no_confirm:
                [jobs.append(i[0].asin) for i in full_match or match]
            else:
                choices = []
                for i in full_match or match:
                    a = i[0].asin
                    t = i[0].full_title
                    c = questionary.Choice(title=f"{a} # {t}", value=a)
                    choices.append(c)

                answer = await questionary.checkbox(
                    f"Found the following matches for '{title}'. Which you want to download?",
                    choices=choices
                ).unsafe_ask_async()
                if answer is not None:
                    [jobs.append(i) for i in answer]

        else:
            logger.error(
                f"Skip title {title}: Not found in library"
            )

    queue = asyncio.Queue()
    for job in jobs:
        item = library.get_item_by_asin(job)
        items = [item]
        odir = pathlib.Path(output_dir)

        if not ignore_podcasts and item.is_parent_podcast():
            items.remove(item)
            if item._children is None:
                await item.get_child_items(
                    start_date=start_date, end_date=end_date
                )

            for i in item._children:
                if i.asin not in jobs:
                    items.append(i)

            podcast_dir = item.create_base_filename(filename_mode)
            odir = output_dir / podcast_dir
            if not odir.is_dir():
                odir.mkdir(parents=True)

        for item in items:
            queue_job(
                queue=queue,
                get_cover=get_cover,
                get_pdf=get_pdf,
                get_annotation=get_annotation,
                get_chapters=get_chapters,
                get_aax=get_aax,
                get_aaxc=get_aaxc,
                client=client,
                output_dir=odir,
                filename_mode=filename_mode,
                item=item,
                cover_sizes=cover_sizes,
                quality=quality,
                overwrite_existing=overwrite_existing,
                aax_fallback=aax_fallback
            )

    try:
        # schedule the consumer
        consumers = [
            asyncio.ensure_future(consume(queue, ignore_errors)) for _ in range(sim_jobs)
        ]
        # wait until the consumer has processed all items
        await queue.join()

    finally:
        # the consumer is still awaiting an item, cancel it
        for consumer in consumers:
            consumer.cancel()

        await asyncio.gather(*consumers, return_exceptions=True)
        display_counter()
