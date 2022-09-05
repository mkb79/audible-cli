from datetime import datetime
from pathlib import Path


class AudibleCliException(Exception):
    """Base class for all errors"""


class NotFoundError(AudibleCliException):
    """Raised if an item is not found"""


class NotDownloadableAsAAX(AudibleCliException):
    """Raised if an item is not downloadable in aax format"""


class FileDoesNotExists(AudibleCliException):
    """Raised if a file does not exist"""

    def __init__(self, file):
        if isinstance(file, Path):
            file = str(file.resolve())

        message = f"{file} does not exist"
        super().__init__(message)


class DirectoryDoesNotExists(AudibleCliException):
    """Raised if a directory does not exist"""

    def __init__(self, path):
        if isinstance(path, Path):
            path = str(path.resolve())

        message = f"{path} does not exist"
        super().__init__(message)


class ProfileAlreadyExists(AudibleCliException):
    """Raised if an item is not found"""

    def __init__(self, name):
        message = f"Profile {name} already exist"
        super().__init__(message)


class LicenseDenied(AudibleCliException):
    """Raised if a license request is not granted"""


class NoDownloadUrl(AudibleCliException):
    """Raised if a license response does not contain a download url"""

    def __init__(self, asin):
        message = f"License response for {asin} does not contain a download url"
        super().__init__(message)


class DownloadUrlExpired(AudibleCliException):
    """Raised if a download url is expired"""

    def __init__(self, lr_file):
        message = f"Download url in {lr_file} is expired."
        super().__init__(message)


class VoucherNeedRefresh(AudibleCliException):
    """Raised if a voucher reached his refresh date"""

    def __init__(self, lr_file):
        message = f"Refresh date for voucher {lr_file} reached."
        super().__init__(message)


class ItemNotPublished(AudibleCliException):
    """Raised if a voucher reached his refresh date"""

    def __init__(self, asin: str, pub_date):
        pub_date = datetime.strptime(pub_date, "%Y-%m-%dT%H:%M:%SZ")
        now = datetime.utcnow()
        published_in = pub_date - now

        pub_str = ""
        if published_in.days > 0:
            pub_str += f"{published_in.days} days, "

        seconds = published_in.seconds
        hours, remainder = divmod(seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        hms = "{:02}h:{:02}m:{:02}s".format(int(hours), int(minutes), int(seconds))
        pub_str += hms

        message = f"{asin} is not published. It will be available in {pub_str}"
        super().__init__(message)
