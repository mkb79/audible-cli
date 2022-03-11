from pathlib import Path


class AudibleCliException(Exception):
    """Base class for all errors"""


class NotFoundError(AudibleCliException):
    """Raised if an item is not found"""


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
