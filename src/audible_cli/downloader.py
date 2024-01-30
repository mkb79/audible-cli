import logging
import pathlib
import re
from enum import Enum, auto
from typing import Any, Callable, Dict, List, NamedTuple, Optional, Union

import aiofiles
import click
import httpx
import tqdm
from aiofiles.os import path, unlink

try:
    from typing import Literal
except ImportError:
    from typing_extensions import Literal


FileMode = Literal["ab", "wb"]

logger = logging.getLogger("audible_cli.downloader")

ACCEPT_RANGES_HEADER = "Accept-Ranges"
ACCEPT_RANGES_NONE_VALUE = "none"
CONTENT_LENGTH_HEADER = "Content-Length"
CONTENT_TYPE_HEADER = "Content-Type"
MAX_FILE_READ_SIZE = 3 * 1024 * 1024
ETAG_HEADER = "ETag"


class ETag:
    def __init__(self, etag: str) -> None:
        self._etag = etag

    @property
    def value(self) -> str:
        return self._etag

    @property
    def parsed_etag(self) -> str:
        return re.search('"([^"]*)"', self.value).group(1)

    @property
    def is_weak(self) -> bool:
        return bool(re.search("^W/", self.value))


class File:
    def __init__(self, file: Union[pathlib.Path, str]) -> None:
        if not isinstance(file, pathlib.Path):
            file = pathlib.Path(file)
        self._file = file

    @property
    def path(self) -> pathlib.Path:
        return self._file

    async def get_size(self) -> int:
        if await path.isfile(self.path):
            return await path.getsize(self.path)
        return 0

    async def remove(self) -> None:
        if await path.isfile(self.path):
            await unlink(self.path)

    async def directory_exists(self) -> bool:
        return await path.isdir(self.path.parent)

    async def is_file(self) -> bool:
        return await path.isfile(self.path) and not await self.is_link()

    async def is_link(self) -> bool:
        return await path.islink(self.path)

    async def exists(self) -> bool:
        return await path.exists(self.path)

    async def read_text_content(
        self, max_bytes: int = MAX_FILE_READ_SIZE, encoding: str = "utf-8", errors=None
    ) -> str:
        file_size = await self.get_size()
        read_size = min(max_bytes, file_size)
        try:
            async with aiofiles.open(
                file=self.path, mode="r", encoding=encoding, errors=errors
            ) as file:
                return await file.read(read_size)
        except Exception:  # noqa
            return "Unknown"


class ResponseInfo:
    def __init__(self, response: httpx.Response) -> None:
        self._response = response
        self.headers: httpx.Headers = response.headers
        self.status_code: int = response.status_code
        self.content_length: Optional[int] = self._get_content_length(self.headers)
        self.content_type: Optional[str] = self._get_content_type(self.headers)
        self.accept_ranges: bool = self._does_accept_ranges(self.headers)
        self.etag: Optional[ETag] = self._get_etag(self.headers)

    @property
    def response(self) -> httpx.Response:
        return self._response

    def supports_resume(self) -> bool:
        return bool(self.accept_ranges)

    @staticmethod
    def _does_accept_ranges(headers: httpx.Headers) -> bool:
        # 'Accept-Ranges' indicates if the source accepts range requests,
        # that let you retrieve a part of the response
        accept_ranges_value = headers.get(
            ACCEPT_RANGES_HEADER, ACCEPT_RANGES_NONE_VALUE
        )
        does_accept_ranges = accept_ranges_value != ACCEPT_RANGES_NONE_VALUE

        return does_accept_ranges

    @staticmethod
    def _get_content_length(headers: httpx.Headers) -> Optional[int]:
        content_length = headers.get(CONTENT_LENGTH_HEADER)

        if content_length is not None:
            return int(content_length)

        return content_length

    @staticmethod
    def _get_content_type(headers: httpx.Headers) -> Optional[str]:
        return headers.get(CONTENT_TYPE_HEADER)

    @staticmethod
    def _get_etag(headers: httpx.Headers) -> Optional[ETag]:
        etag_header = headers.get(ETAG_HEADER)
        if etag_header is None:
            return etag_header
        return ETag(etag_header)


class Status(Enum):
    Success = auto()
    DestinationAlreadyExists = auto()
    DestinationFolderNotExists = auto()
    DestinationNotAFile = auto()
    DownloadError = auto()
    DownloadErrorStatusCode = auto()
    DownloadSizeMismatch = auto()
    DownloadContentTypeMismatch = auto()
    DownloadIndividualParts = auto()
    SourceDoesNotSupportResume = auto()
    StatusCode = auto()


async def check_target_file_status(
    target_file: File, force_reload: bool, **kwargs: Any
) -> Status:
    if not await target_file.directory_exists():
        logger.error(
            f"Folder {target_file.path} does not exists! Skip download."
        )
        return Status.DestinationFolderNotExists

    if await target_file.exists() and not await target_file.is_file():
        logger.error(
            f"Object {target_file.path} exists but is not a file. Skip download."
        )
        return Status.DestinationNotAFile

    if await target_file.is_file() and not force_reload:
        logger.info(
            f"File {target_file.path} already exists. Skip download."
        )
        return Status.DestinationAlreadyExists

    return Status.Success


async def check_download_size(
    tmp_file: File, target_file: File, head_response: ResponseInfo, **kwargs: Any
) -> Status:
    tmp_file_size = await tmp_file.get_size()
    content_length = head_response.content_length

    if tmp_file_size is not None and content_length is not None:
        if tmp_file_size != content_length:
            logger.error(
                f"Error downloading {target_file.path}. File size missmatch. "
                f"Expected size: {content_length}; Downloaded: {tmp_file_size}"
            )
        return Status.DownloadSizeMismatch

    return Status.Success


async def check_status_code(
    response: ResponseInfo, tmp_file: File, target_file: File, **kwargs: Any
) -> Status:
    if not 200 <= response.status_code < 400:
        content = await tmp_file.read_text_content()
        logger.error(
            f"Error downloading {target_file.path}. Message: {content}"
        )
        return Status.StatusCode

    return Status.Success


async def check_content_type(
    response: ResponseInfo, target_file: File, tmp_file: File,
    expected_types: List[str], **kwargs: Any
) -> Status:
    if not expected_types:
        return Status.Success

    if response.content_type not in expected_types:
        content = await tmp_file.read_text_content()
        logger.error(
            f"Error downloading {target_file.path}. Wrong content type. "
            f"Expected type(s): {expected_types}; "
            f"Got: {response.content_type}; Message: {content}"
        )
        return Status.DownloadContentTypeMismatch

    return Status.Success


def _status_for_message(message: str) -> Status:
    if "please download individual parts" in message:
        return Status.DownloadIndividualParts
    return Status.Success


async def check_status_for_message(
    response: ResponseInfo, tmp_file: File, **kwargs: Any
) -> Status:
    if response.content_type and "text" in response.content_type:
        length = response.content_length or await tmp_file.get_size()
        if length <= MAX_FILE_READ_SIZE:
            message = await tmp_file.read_text_content()
            return _status_for_message(message)
        
    return Status.Success


class DownloadResult(NamedTuple):
    status: Status
    destination: File
    head_response: Optional[ResponseInfo]
    response: Optional[ResponseInfo]
    message: Optional[str]


class DummyProgressBar:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        pass

    def update(self, *args, **kwargs):
        pass


def get_progressbar(
    destination: pathlib.Path, total: Optional[int], start: int = 0
) -> Union[tqdm.tqdm, DummyProgressBar]:
    if total is None:
        return DummyProgressBar()

    description = click.format_filename(destination, shorten=True)
    progressbar = tqdm.tqdm(
        desc=description,
        total=total,
        unit="B",
        unit_scale=True,
        unit_divisor=1024
    )
    if start > 0:
        progressbar.update(start)

    return progressbar


class Downloader:

    MIN_STREAM_LENGTH = 10*1024*1024  # using stream mode if source is greater than
    MIN_RESUME_FILE_LENGTH = 10*1024*1024  # keep resume file if file is greater than
    RESUME_SUFFIX = ".resume"
    TMP_SUFFIX = ".tmp"

    def __init__(
        self,
        source: httpx.URL,
        client: httpx.AsyncClient,
        expected_types: Optional[Union[List[str], str]] = None,
        additional_headers: Optional[Dict[str, str]] = None
    ) -> None:
        self._source = source
        self._client = client
        self._expected_types = self._normalize_expected_types(expected_types)
        self._additional_headers = self._normalize_headers(additional_headers)
        self._head_request: Optional[ResponseInfo] = None

    @staticmethod
    def _normalize_expected_types(
        expected_types: Optional[Union[List[str], str]]
    ) -> List[str]:
        if not isinstance(expected_types, list):
            if expected_types is None:
                expected_types = []
            else:
                expected_types = [expected_types]
        return expected_types

    @staticmethod
    def _normalize_headers(headers: Optional[Dict[str, str]]) -> Dict[str, str]:
        if headers is None:
            return {}
        return headers

    async def get_head_response(self, force_recreate: bool = False) -> ResponseInfo:
        if self._head_request is None or force_recreate:
            head_response = await self._client.head(
                self._source, headers=self._additional_headers, follow_redirects=True,
            )
            self._head_request = ResponseInfo(head_response)

        return self._head_request

    async def _determine_resume_file(self, target_file: File) -> File:
        head_response = await self.get_head_response()
        etag = head_response.etag
        resume_name = target_file.path if etag is None else etag.parsed_etag
        resume_file = pathlib.Path(resume_name).with_suffix(self.RESUME_SUFFIX)
        return File(resume_file)

    def _determine_tmp_file(self, target_file: File) -> File:
        tmp_file = pathlib.Path(target_file.path).with_suffix(self.TMP_SUFFIX)
        return File(tmp_file)

    async def _handle_tmp_file(
        self, tmp_file: File, supports_resume: bool, response: ResponseInfo
    ) -> None:
        tmp_file_size = await tmp_file.get_size()
        expected_size = response.content_length

        if (
            supports_resume and expected_size is not None
            and self.MIN_RESUME_FILE_LENGTH < tmp_file_size < expected_size
        ):
            logger.debug(f"Keep resume file {tmp_file.path}")
        else:
            await tmp_file.remove()

    @staticmethod
    async def _rename_file(
        tmp_file: File, target_file: File, force_reload: bool, response: ResponseInfo
    ) -> Status:
        target_path = target_file.path

        if await target_file.exists() and force_reload:
            i = 0
            while target_path.with_suffix(f"{target_path.suffix}.old.{i}").exists():
                i += 1
            target_path.rename(target_path.with_suffix(f"{target_path.suffix}.old.{i}"))

        tmp_file.path.rename(target_path)
        logger.info(
            f"File {target_path} downloaded in {response.response.elapsed}."
        )
        return Status.Success

    @staticmethod
    async def _check_and_return_download_result(
        status_check_func: Callable,
        tmp_file: File,
        target_file: File,
        response: ResponseInfo,
        head_response: ResponseInfo,
        expected_types: List[str]
    ) -> Optional[DownloadResult]:
        status = await status_check_func(
            response=response,
            tmp_file=tmp_file,
            target_file=target_file,
            expected_types=expected_types
        )
        if status != Status.Success:
            message = await tmp_file.read_text_content()
            return DownloadResult(
                status=status,
                destination=target_file,
                head_response=head_response,
                response=response,
                message=message
            )
        return None

    async def _postprocessing(
        self, tmp_file: File, target_file: File, response: ResponseInfo,
        force_reload: bool
    ) -> DownloadResult:
        head_response = await self.get_head_response()

        status_checks = [
            check_status_for_message,
            check_status_code,
            check_status_code,
            check_content_type
        ]
        for check in status_checks:
            result = await self._check_and_return_download_result(
                check, tmp_file, target_file, response,
                head_response, self._expected_types
            )
            if result:
                return result

        await self._rename_file(
            tmp_file=tmp_file,
            target_file=target_file,
            force_reload=force_reload,
            response=response,
        )

        return DownloadResult(
            status=Status.Success,
            destination=target_file,
            head_response=head_response,
            response=response,
            message=None
        )

    async def _stream_download(
        self,
        tmp_file: File,
        target_file: File,
        start: int,
        progressbar: Union[tqdm.tqdm, DummyProgressBar],
        force_reload: bool = True
    ) -> DownloadResult:
        headers = self._additional_headers.copy()
        if start > 0:
            headers.update(Range=f"bytes={start}-")
            file_mode: FileMode = "ab"
        else:
            file_mode: FileMode = "wb"

        async with self._client.stream(
            method="GET", url=self._source, follow_redirects=True, headers=headers
        ) as response:
            with progressbar:
                async with aiofiles.open(tmp_file.path, mode=file_mode) as file:
                    async for chunk in response.aiter_bytes():
                        await file.write(chunk)
                        progressbar.update(len(chunk))

            return await self._postprocessing(
                tmp_file=tmp_file,
                target_file=target_file,
                response=ResponseInfo(response=response),
                force_reload=force_reload
            )

    async def _download(
        self, tmp_file: File, target_file: File, start: int, force_reload: bool
    ) -> DownloadResult:
        headers = self._additional_headers.copy()
        if start > 0:
            headers.update(Range=f"bytes={start}-")
            file_mode: FileMode = "ab"
        else:
            file_mode: FileMode = "wb"

        response = await self._client.get(
            self._source, follow_redirects=True, headers=headers
        )
        async with aiofiles.open(tmp_file.path, mode=file_mode) as file:
            await file.write(response.content)

        return await self._postprocessing(
            tmp_file=tmp_file,
            target_file=target_file,
            response=ResponseInfo(response=response),
            force_reload=force_reload
        )

    async def run(
        self,
        target: pathlib.Path,
        force_reload: bool = False
    ) -> DownloadResult:
        target_file = File(target)
        destination_status = await check_target_file_status(
            target_file, force_reload
        )
        if destination_status != Status.Success:
            return DownloadResult(
                status=destination_status,
                destination=target_file,
                head_response=None,
                response=None,
                message=None
            )

        head_response = await self.get_head_response()
        supports_resume = head_response.supports_resume()
        if supports_resume:
            tmp_file = await self._determine_resume_file(target_file=target_file)
            start = await tmp_file.get_size()
        else:
            tmp_file = self._determine_tmp_file(target_file=target_file)
            await tmp_file.remove()
            start = 0

        should_stream = False
        progressbar = None
        if (
                head_response.content_length is not None and
                head_response.content_length >= self.MIN_STREAM_LENGTH
        ):
            should_stream = True
            progressbar = get_progressbar(
                target_file.path, head_response.content_length, start
            )

        try:
            if should_stream:
                return await self._stream_download(
                    tmp_file=tmp_file,
                    target_file=target_file,
                    start=start,
                    progressbar=progressbar,
                    force_reload=force_reload
                )
            else:
                return await self._download(
                    tmp_file=tmp_file,
                    target_file=target_file,
                    start=start,
                    force_reload=force_reload
                )
        finally:
            await self._handle_tmp_file(
                tmp_file=tmp_file,
                supports_resume=supports_resume,
                response=head_response
            )
