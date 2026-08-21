"""Console logging for the command line.

Diagnostics go to stderr. stdout carries what a command was asked to
produce -- a JSON response, an exported library, a list of titles -- and a
shell has to be able to keep the two apart. ``audible api library | jq``
must not be handed an error message, and ``audible library list > books``
must not collect progress notes along with the titles.
"""

import logging
import os
import pathlib
import threading
import warnings

import click


#: The logger every module in the package writes to. Handlers hang off this
#: one, so a level set here reaches all of them.
LOGGER_NAME = "audible_cli"

#: Names for the handlers installed below. A handler is looked up by name
#: before a new one is added, so setting a logger up twice replaces the
#: handler instead of printing every line twice.
CONSOLE_HANDLER = "audible-cli-console"

#: Prefix for file handler names. The destination is appended, so the same
#: path replaces its handler while a second path adds one.
FILE_HANDLER = "audible-cli-file"

#: Layout for the handlers attached through `log_helper`, where the extra
#: context earns its width. The handler the command line runs on stays terse.
RECORD_FORMAT = (
    "%(asctime)s %(levelname)s [%(name)s] %(filename)s:%(lineno)d: %(message)s"
)

#: Colour per level. INFO is absent on purpose: it is the level the
#: commands narrate at, and a prefix in front of every ordinary line is
#: noise rather than information.
LEVEL_COLORS = {
    logging.DEBUG: "blue",
    logging.WARNING: "yellow",
    logging.ERROR: "red",
    logging.CRITICAL: "red",
}


#: Held while handlers are exchanged. Removing and adding are two steps,
#: and two threads arriving together would otherwise both remove before
#: either adds, leaving the logger with two handlers of the same name.
_handler_lock = threading.RLock()

#: What `py.warnings` propagated before `capture_warnings` touched it.
_warnings_propagate: bool | None = None

audible_cli_logger = logging.getLogger(LOGGER_NAME)

# Importing the package must configure nothing, and must not leave Python
# falling back to `lastResort`. The command line adds its own handler in
# `click_basic_config`.
audible_cli_logger.addHandler(logging.NullHandler())


def _color_wanted() -> bool | None:
    """Say whether colour is wanted, or None to let click read the stream.

    Click decides by asking whether the stream is a terminal, which is the
    right default but ignores the two environment variables a shell user
    reaches for (see no-color.org). Honour those, and leave the terminal
    question to click. NO_COLOR wins when both are set: somebody who
    cannot read the colour is the one to listen to.

    Returns:
        True to keep colour, False to drop it, None to let click decide.
    """
    if os.environ.get("NO_COLOR"):
        return False

    if os.environ.get("FORCE_COLOR"):
        return True

    return None


class ColorFormatter(logging.Formatter):
    """Put the level in front of the levels in :data:`LEVEL_COLORS`.

    Only the message is prefixed. A traceback keeps its own shape, because
    :meth:`logging.Formatter.format` appends it after calling this method
    for the message alone.
    """

    def formatMessage(self, record: logging.LogRecord) -> str:  # noqa: N802
        """Render the message, prefixing every line of it."""
        message = super().formatMessage(record)
        color = LEVEL_COLORS.get(record.levelno)
        if color is None:
            return message

        prefix = click.style(f"{record.levelname.lower()}: ", fg=color)
        return "\n".join(prefix + line for line in message.splitlines())


class ClickEchoHandler(logging.Handler):
    """Write records to stderr, through click.

    ``click.echo`` rather than a plain stream write: it decides about the
    colour escapes by looking at the destination, and on Windows it goes
    through colorama, where they would otherwise be printed as text.
    """

    def emit(self, record: logging.LogRecord) -> None:
        """Write one record.

        A failure on the way out is reported through logging, never raised
        into the command that happened to log at the wrong moment. Hence
        the bare except and :meth:`handleError`.
        """
        try:
            click.echo(self.format(record), err=True, color=_color_wanted())
        except Exception:
            self.handleError(record)


def _normalize_logger(logger: logging.Logger | str | None) -> logging.Logger:
    """Take a logger, its name, or None for the root, and return the logger."""
    if not isinstance(logger, logging.Logger):
        logger = logging.getLogger(logger)

    return logger


def _set_level(target: logging.Logger | logging.Handler, level: str | int) -> None:
    """Set a level on a logger or a handler, by name or by number."""
    target.setLevel(level.upper() if isinstance(level, str) else level)


def _warn_if_out_of_reach(handler: logging.Handler) -> None:
    """Say so when a handler sits below the logger it hangs on.

    Such a handler never sees the records it was lowered for: the logger
    drops them first. This goes through :mod:`warnings` rather than the
    logger, because the logger level being diagnosed is exactly the one
    that would swallow the diagnosis.

    Args:
        handler: The handler that was just attached.
    """
    if not 0 < handler.level < audible_cli_logger.level:
        return

    warnings.warn(
        f"{handler.get_name()} is set to "
        f"{logging.getLevelName(handler.level)}, below the {LOGGER_NAME} "
        f"logger at {logging.getLevelName(audible_cli_logger.level)}, so it "
        f"will not see those records.",
        stacklevel=3,
    )


def _attach(handler: logging.Handler, name: str, level: str | int | None) -> None:
    """Attach a handler under a name, replacing an earlier one of that name.

    Args:
        handler: The handler to attach.
        name: The name it is known by.
        level: A level for the handler, or None to let the logger decide.
    """
    handler.set_name(name)
    handler.setFormatter(logging.Formatter(RECORD_FORMAT))

    with _handler_lock:
        _detach(name)
        audible_cli_logger.addHandler(handler)

    if level is not None:
        _set_level(handler, level)
        _warn_if_out_of_reach(handler)


def _detach(name: str) -> None:
    """Remove any handler carrying a name."""
    with _handler_lock:
        for handler in list(audible_cli_logger.handlers):
            if handler.get_name() == name:
                audible_cli_logger.removeHandler(handler)
                handler.close()


class AudibleCliLogHelper:
    """The logging controls the package offers to plugins and embedders."""

    @staticmethod
    def set_level(level: str | int) -> None:
        """Set the log level for the audible_cli package.

        Args:
            level: A level name or number.
        """
        _set_level(audible_cli_logger, level)

    @staticmethod
    def set_console_logger(level: str | int | None = None) -> None:
        """Write the package log to stderr, in full detail.

        Replaces the terse handler the command line installs: there is room
        for one console log, not two saying the same thing twice. This one
        writes straight to the stream, so it carries no colour and does not
        go through colorama on Windows.

        Args:
            level: A level for this handler, or None to follow the package.
        """
        _attach(logging.StreamHandler(), CONSOLE_HANDLER, level)

    @staticmethod
    def set_file_logger(
        filename: str | pathlib.Path, level: str | int | None = None
    ) -> None:
        """Write the package log to a file, in full detail.

        Naming a file twice replaces its handler. Naming a second file adds
        one, so a caller can keep an audit log and a debug log side by side.

        Args:
            filename: Where to write. The file is appended to.
            level: A level for this handler, or None to follow the package.
        """
        path = pathlib.Path(filename)
        handler = logging.FileHandler(path, encoding="utf-8")
        _attach(handler, f"{FILE_HANDLER}:{path.resolve()}", level)

    @staticmethod
    def capture_warnings(status: bool = True) -> None:
        """Route :func:`warnings.warn` through logging.

        Python hands captured warnings to the ``py.warnings`` logger, which
        is not this package's and is left without a handler that writes
        anywhere. Give it the console handler, or the capture would be a
        way of silencing warnings rather than of collecting them.

        Handing the warnings back puts that logger's propagation the way it
        was found: left off with no handler attached, a later
        ``captureWarnings(True)`` by anybody else would route into silence.

        Args:
            status: True to capture, False to hand warnings back.
        """
        global _warnings_propagate  # noqa: PLW0603

        logging.captureWarnings(status)

        warnings_logger = logging.getLogger("py.warnings")
        with _handler_lock:
            if status:
                if _warnings_propagate is None:
                    _warnings_propagate = warnings_logger.propagate
                click_basic_config(warnings_logger)
                return

            for handler in list(warnings_logger.handlers):
                if handler.get_name() == CONSOLE_HANDLER:
                    warnings_logger.removeHandler(handler)

            if _warnings_propagate is not None:
                warnings_logger.propagate = _warnings_propagate
                _warnings_propagate = None


log_helper = AudibleCliLogHelper()


def click_basic_config(logger: logging.Logger | str | None = None) -> logging.Logger:
    """Give a logger the terse, coloured, stderr-bound console handler.

    This is what the command line runs on. Handlers attached by anybody
    else are left alone; only an earlier one of ours is replaced.

    Args:
        logger: The logger to configure, by name or by object.

    Returns:
        The logger, configured.
    """
    logger = _normalize_logger(logger)

    handler = ClickEchoHandler()
    handler.set_name(CONSOLE_HANDLER)
    handler.setFormatter(ColorFormatter())

    with _handler_lock:
        for attached in list(logger.handlers):
            if attached.get_name() == CONSOLE_HANDLER:
                logger.removeHandler(attached)

        logger.addHandler(handler)

    logger.propagate = False

    return logger
