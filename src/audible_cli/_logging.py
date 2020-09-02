import logging
import pathlib
from typing import Optional, Union
from warnings import warn

LEVELS = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warn": logging.WARNING,
    "warning": logging.WARNING,
    "error": logging.ERROR,
    "critical": logging.CRITICAL,
    "not-set": logging.NOTSET
}


class AudibleLogHelper:
    def __init__(self) -> None:
        self._formatter = logging.Formatter(
            "%(asctime)s %(levelname)s [%(name)s] "
            "%(filename)s:%(lineno)d: %(message)s"
        )
        self._logger = logging.getLogger("audible-cli")

    def set_level(self, level: Union[str, int]) -> None:
        """Set logging level for the main logger."""
        if isinstance(level, str):
            level = LEVELS.get(level.lower().strip())

        self._logger.setLevel(level)
        self._logger.info(
            "set logging threshold to \"%s\"",
            logging.getLevelName(self._logger.level)
        )

    def _set_handler_level(self, handler,
                           level: Optional[Union[str, int]]) -> None:
        if isinstance(level, str):
            level = LEVELS.get(level.lower().strip())

        if level:
            handler.setLevel(level)

        self._logger.info(
            f"set logging threshold for \"{handler.name}\" "
            f"to \"{logging.getLevelName(handler.level)}\""
        )

        if handler.level < self._logger.level:
            warn("Handler level must be equal or greater than logger level")

    def set_console_logger(self,
                           level: Optional[Union[str, int]] = None) -> None:
        """Set logging level for the stream handler."""
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(self._formatter)
        stream_handler.set_name("ConsoleLogger")
        self._logger.addHandler(stream_handler)
        self._set_handler_level(stream_handler, level)

    def set_file_logger(
            self, filename: str, level: Optional[Union[str, int]] = None
    ) -> None:
        """Set logging level and filename for the file handler."""
        filename = pathlib.Path(filename)
        file_handler = logging.FileHandler(filename)
        file_handler.setFormatter(self._formatter)
        file_handler.set_name("FileLogger")
        self._logger.addHandler(file_handler)
        self._set_handler_level(file_handler, level)

    def capture_warnings(self, status: bool = True) -> None:
        logging.captureWarnings(status)
        self._logger.info(
            f"Capture warnings {'activated' if status else 'deactivated'}"
        )
