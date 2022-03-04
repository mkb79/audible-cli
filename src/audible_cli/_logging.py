import logging
import pathlib
from typing import Optional, Union
from warnings import warn

import click


audible_cli_logger = logging.getLogger("audible_cli")
audible_cli_logger.addHandler(logging.NullHandler())

log_formatter = logging.Formatter(
    "%(asctime)s %(levelname)s [%(name)s] %(filename)s:%(lineno)d: %(message)s"
)


class AudibleCliLogHelper:
    def set_level(self, level: Union[str, int]) -> None:
        """Set logging level for the audible-cli package."""
        self._set_level(audible_cli_logger, level)

    @staticmethod
    def _set_level(obj, level: Optional[Union[str, int]]) -> None:
        if level:
            level = level.upper() if isinstance(level, str) else level
            obj.setLevel(level)

        level_name = logging.getLevelName(obj.level)
        audible_cli_logger.info(
            f"set log level for {obj.name} to: {level_name}"
        )

        if 0 < obj.level < audible_cli_logger.level:
            warn(
                f"{obj.name} level is lower than "
                f"{audible_cli_logger.name} logger level"
            )

    def _set_handler(self, handler, name, level):
        handler.setFormatter(log_formatter)
        handler.set_name(name)
        audible_cli_logger.addHandler(handler)
        self._set_level(handler, level)

    def set_console_logger(
            self,
            level: Optional[Union[str, int]] = None
    ) -> None:
        """Set up a console logger to the audible-cli package."""
        handler = logging.StreamHandler()
        # noinspection PyTypeChecker
        self._set_handler(handler, "ConsoleLogger", level)

    def set_file_logger(
            self, filename: str, level: Optional[Union[str, int]] = None
    ) -> None:
        """Set up a file logger to the audible-cli package."""
        filename = pathlib.Path(filename)
        handler = logging.FileHandler(filename)
        # noinspection PyTypeChecker
        self._set_handler(handler, "FileLogger", level)

    @staticmethod
    def capture_warnings(status: bool = True) -> None:
        """Lets the logger capture warnings."""
        logging.captureWarnings(status)
        audible_cli_logger.info(
            f"Capture warnings {'activated' if status else 'deactivated'}"
        )


log_helper = AudibleCliLogHelper()


# copied from https://github.com/Toilal/click-logging

def click_verbosity_option(logger=None, *names, **kwargs):
    """A decorator that adds a `--verbosity, -v` option to the decorated
    command.
    Name can be configured through ``*names``. Keyword arguments are passed to
    the underlying ``click.option`` decorator.
    """

    if not names:
        names = ["--verbosity", "-v"]

    kwargs.setdefault("default", "INFO")
    kwargs.setdefault("metavar", "LVL")
    kwargs.setdefault("expose_value", False)
    kwargs.setdefault(
        "help", "Either CRITICAL, ERROR, WARNING, "
        "INFO or DEBUG. [default: INFO]"
    )
    kwargs.setdefault("is_eager", True)

    logger = _normalize_logger(logger)

    def decorator(f):
        def _set_level(ctx, param, value):
            x = getattr(logging, value.upper(), None)
            if x is None:
                raise click.BadParameter(
                    f"Must be CRITICAL, ERROR, WARNING, INFO or DEBUG, "
                    f"not {value}"
                )
            logger.setLevel(x)

        return click.option(*names, callback=_set_level, **kwargs)(f)
    return decorator


class ColorFormatter(logging.Formatter):
    def __init__(self, style_kwargs):
        self.style_kwargs = style_kwargs
        super().__init__()

    def format(self, record):
        if not record.exc_info:
            level = record.levelname.lower()
            msg = record.getMessage()
            if self.style_kwargs.get(level):
                prefix = click.style(
                    f"{level}: ",
                    **self.style_kwargs[level])
                msg = "\n".join(prefix + x for x in msg.splitlines())
            return msg
        return super().format(record)


class ClickHandler(logging.Handler):
    def __init__(self, echo_kwargs):
        super().__init__()
        self.echo_kwargs = echo_kwargs

    def emit(self, record):
        try:
            msg = self.format(record)
            level = record.levelname.lower()
            if self.echo_kwargs.get(level):
                click.echo(msg, **self.echo_kwargs[level])
            else:
                click.echo(msg)
        except Exception:
            self.handleError(record)


def _normalize_logger(logger):
    if not isinstance(logger, logging.Logger):
        logger = logging.getLogger(logger)
    return logger


def _normalize_style_kwargs(styles):
    normalized_styles = {
        "error": dict(fg="red"),
        "exception": dict(fg="red"),
        "critical": dict(fg="red"),
        "debug": dict(fg="blue"),
        "warning": dict(fg="yellow")
    }
    if styles:
        normalized_styles.update(styles)
    return normalized_styles


def _normalize_echo_kwargs(echo_kwargs):
    normamized_echo_kwargs = dict()
    if echo_kwargs:
        normamized_echo_kwargs.update(echo_kwargs)
    return normamized_echo_kwargs


def click_basic_config(logger=None, style_kwargs=None, echo_kwargs=None):
    """Set up the default handler (:py:class:`ClickHandler`) and formatter
    (:py:class:`ColorFormatter`) on the given logger."""
    logger = _normalize_logger(logger)
    style_kwargs = _normalize_style_kwargs(style_kwargs)
    echo_kwargs = _normalize_echo_kwargs(echo_kwargs)

    handler = ClickHandler(echo_kwargs)
    handler.formatter = ColorFormatter(style_kwargs)
    logger.handlers = [handler]
    logger.propagate = False

    return logger
