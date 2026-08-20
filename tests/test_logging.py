"""The log has to stay out of the way of the payload.

Every command writes its result to stdout: a JSON response, an exported
library, a list of titles. A diagnostic printed to the same place ends up
inside the result, and a shell has no way to tell which line was which.
The rule these tests hold is the plain one -- stdout is the answer, stderr
is everything said about producing it.
"""

import logging
import warnings

import click
import pytest
from click.testing import CliRunner

from audible_cli import _logging


@pytest.fixture(autouse=True)
def colour_neutral(monkeypatch):
    """Take the developer's colour preference out of the picture.

    Most of these tests compare stderr to an exact string. With FORCE_COLOR
    set in the shell that ran pytest, click keeps the escapes and every one
    of those comparisons fails for a reason that has nothing to do with the
    code under test.
    """
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("FORCE_COLOR", raising=False)


@pytest.fixture
def log():
    """A freshly configured package logger, put back afterwards."""
    logger = logging.getLogger("audible_cli")
    handlers, level, propagate = logger.handlers[:], logger.level, logger.propagate

    _logging.click_basic_config(logger)
    logger.setLevel(logging.DEBUG)
    yield logger

    for handler in logger.handlers:
        if handler not in handlers:
            handler.close()
    logger.handlers[:] = handlers
    logger.setLevel(level)
    logger.propagate = propagate


def run(emit, **invoke_kwargs):
    """Invoke `emit` inside a command, and return the two streams apart."""

    @click.command()
    def cmd():
        click.echo("the payload")
        emit()

    result = CliRunner().invoke(cmd, [], catch_exceptions=False, **invoke_kwargs)
    return result.stdout, result.stderr


def test_the_payload_keeps_stdout_to_itself(log):
    out, err = run(lambda: log.error("something went wrong"))

    assert out == "the payload\n"
    assert "something went wrong" in err


def test_every_level_goes_the_same_way(log):
    for level in ("debug", "info", "warning", "error", "critical"):
        out, err = run(lambda level=level: getattr(log, level)("a line"))

        assert out == "the payload\n", f"{level} leaked into stdout"
        assert "a line" in err, f"{level} was not written at all"


def test_a_module_logger_reaches_the_same_handler(log):
    # What every module in the package actually does: getLogger(__name__),
    # which is a child of the one the handler hangs on.
    child = logging.getLogger("audible_cli.cmds.cmd_download")

    out, err = run(lambda: child.warning("from a command"))

    assert out == "the payload\n"
    assert err == "warning: from a command\n"


def test_the_package_log_does_not_also_go_to_the_root_logger(log):
    # An embedder with root logging configured would otherwise see every
    # line twice, once from each handler.
    root = logging.getLogger()
    collected = []
    catcher = logging.Handler()
    catcher.emit = collected.append
    root.addHandler(catcher)
    try:
        run(lambda: log.error("once"))
    finally:
        root.removeHandler(catcher)

    assert collected == []


def test_only_the_levels_worth_naming_carry_a_prefix(log):
    # info is what the commands narrate at. Prefixing every ordinary line
    # with "info: " says nothing the reader did not already know.
    _, info = run(lambda: log.info("ordinary progress"))

    assert info == "ordinary progress\n"

    for level, prefix in (
        ("debug", "debug: "),
        ("warning", "warning: "),
        ("error", "error: "),
        ("critical", "critical: "),
    ):
        _, err = run(lambda level=level: getattr(log, level)("a line"))

        assert err == f"{prefix}a line\n"


def test_a_message_of_several_lines_keeps_its_level_on_each(log):
    _, err = run(lambda: log.error("first\nsecond"))

    assert err == "error: first\nerror: second\n"


def test_a_traceback_keeps_its_own_shape(log):
    # The prefix belongs to the message. Stamping "error: " down the side
    # of a traceback makes it harder to read and harder to paste into a
    # bug report.
    def emit():
        try:
            raise ValueError("burst")
        except ValueError:
            log.exception("while trying")

    _, err = run(emit)

    assert err.startswith("error: while trying\n")
    assert "\nTraceback (most recent call last):\n" in err
    assert err.rstrip().endswith("ValueError: burst")
    assert "error: Traceback" not in err


def test_colour_does_not_survive_a_pipe(log):
    # CliRunner hands the command a buffer rather than a terminal, which is
    # what a pipe looks like from the inside.
    _, err = run(lambda: log.error("plain"))

    assert "\x1b[" not in err


def test_a_terminal_gets_its_colour(log):
    _, err = run(lambda: log.error("bright"), color=True)

    assert err == f"{click.style('error: ', fg='red')}bright\n"


def test_no_color_wins_over_the_terminal(log, monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")

    _, err = run(lambda: log.error("plain again"), color=True)

    assert err == "error: plain again\n"


def test_force_color_wins_over_the_pipe(log, monkeypatch):
    monkeypatch.setenv("FORCE_COLOR", "1")

    _, err = run(lambda: log.error("bright anyway"))

    assert "\x1b[" in err


def test_no_color_wins_when_both_are_set(log, monkeypatch):
    # A reader who cannot make out the colour has the stronger claim than
    # a workflow that would like to keep it.
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("FORCE_COLOR", "1")

    _, err = run(lambda: log.error("plain"), color=True)

    assert err == "error: plain\n"


def test_configuring_twice_does_not_say_everything_twice(log):
    _logging.click_basic_config(log)
    _logging.click_basic_config(log)

    _, err = run(lambda: log.error("once"))

    assert err == "error: once\n"


def test_a_failure_while_logging_does_not_reach_the_command():
    # The handler contract: whatever goes wrong on the way out, the command
    # that happened to log carries on. Here the record cannot be rendered
    # at all, because its arguments do not fit its message.
    handler = _logging.ClickEchoHandler()
    handler.setFormatter(_logging.ColorFormatter())
    record = logging.LogRecord(
        "audible_cli", logging.ERROR, __file__, 1, "%d files", ("many",), None
    )

    raising = logging.raiseExceptions
    logging.raiseExceptions = False
    try:
        handler.emit(record)
    finally:
        logging.raiseExceptions = raising


def test_a_detailed_console_log_replaces_the_terse_one(log):
    # Two console handlers would print every line twice, in two different
    # layouts. There is room for one console log at a time.
    _logging.log_helper.set_console_logger()

    consoles = [
        handler
        for handler in log.handlers
        if handler.get_name() == _logging.CONSOLE_HANDLER
    ]

    assert len(consoles) == 1
    assert not isinstance(consoles[0], _logging.ClickEchoHandler)


@pytest.mark.parametrize("level", ["WARNING", "ERROR", "CRITICAL"])
def test_a_handler_below_the_logger_says_so(log, level):
    # The warning goes through `warnings`, not through the logger: at ERROR
    # and above, a logged warning would be dropped by the very level it is
    # trying to report.
    log.setLevel(level)

    with pytest.warns(UserWarning, match="will not see those records"):
        _logging.log_helper.set_console_logger("DEBUG")


def test_the_trap_the_warning_names_is_real(log, tmp_path):
    log.setLevel(logging.WARNING)
    path = tmp_path / "audible.log"

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        _logging.log_helper.set_file_logger(path, "DEBUG")

    log.debug("dropped before the handler sees it")
    for handler in log.handlers:
        handler.flush()

    assert path.read_text(encoding="utf-8") == ""


def test_two_destinations_both_get_the_record(log, tmp_path):
    # Handlers are replaced by name. Naming the file in the handler keeps a
    # second destination from quietly evicting the first.
    audit, debug = tmp_path / "audit.log", tmp_path / "debug.log"
    _logging.log_helper.set_file_logger(audit)
    _logging.log_helper.set_file_logger(debug)

    log.error("failure")
    for handler in log.handlers:
        handler.flush()

    assert "failure" in audit.read_text(encoding="utf-8")
    assert "failure" in debug.read_text(encoding="utf-8")


def test_naming_the_same_file_twice_keeps_one_handler(log, tmp_path):
    path = tmp_path / "audible.log"
    _logging.log_helper.set_file_logger(path)
    _logging.log_helper.set_file_logger(path)

    log.error("failure")
    for handler in log.handlers:
        handler.flush()

    assert path.read_text(encoding="utf-8").count("failure") == 1


def test_the_file_log_is_written_as_utf_8(log, tmp_path):
    # Titles carry umlauts and worse. Left to the platform default, this
    # file is unreadable on a Windows box.
    path = tmp_path / "audible.log"
    _logging.log_helper.set_file_logger(path)

    handler = log.handlers[-1]
    log.warning("Hörbuch: Größenwahn")
    handler.flush()

    assert handler.encoding == "utf-8"
    assert "Hörbuch: Größenwahn" in path.read_text(encoding="utf-8")


def test_captured_warnings_still_come_out_somewhere(log):
    # captureWarnings hands them to the py.warnings logger, which carries a
    # NullHandler and no route anywhere. Capturing must not be a way of
    # silencing them.
    _logging.log_helper.capture_warnings(True)
    try:
        _, err = run(lambda: warnings.warn("something is off", stacklevel=1))
    finally:
        _logging.log_helper.capture_warnings(False)

    assert "something is off" in err


def test_handing_the_warnings_back_stops_the_routing(log):
    _logging.log_helper.capture_warnings(True)
    _logging.log_helper.capture_warnings(False)

    handlers = logging.getLogger("py.warnings").handlers

    assert not any(
        handler.get_name() == _logging.CONSOLE_HANDLER for handler in handlers
    )
