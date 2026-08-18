"""The one lock every terminal writer takes.

What matters is that a whole write arrives whole: a log line must not land
inside the escape sequence a progress bar is drawing, nor the other way
round. Nothing here stands in front of `sys.stdout`, so these are about
the lock and about who takes it.
"""

import logging
import os
import threading
import time

import pytest
import tqdm

from audible_cli import _logging, _terminal, progress


class FakeStream:
    def __init__(self):
        self.writes = []

    def write(self, text):
        self.writes.append(text)
        return len(text)

    def flush(self):
        pass


def a_record():
    return logging.LogRecord("t", logging.WARNING, __file__, 1, "a line", None, None)


def lock_is_free():
    """Whether the lock can be taken, without blocking to find out."""
    taken = threading.Event()

    def probe():
        _terminal.write_lock().acquire()
        taken.set()
        _terminal.write_lock().release()

    thread = threading.Thread(target=probe, daemon=True)
    thread.start()
    thread.join(timeout=0.2)
    return taken.is_set()


def test_the_lock_is_the_one_tqdm_already_keeps():
    # Not a new one. The log handler is already inside it by way of
    # `tqdm.external_write_mode`, so a second lock would leave the two
    # writers unsynchronised while looking synchronised.
    assert _terminal.write_lock() is tqdm.tqdm.get_lock()


def test_the_log_handler_takes_that_same_lock(monkeypatch):
    held = []
    real = tqdm.tqdm.external_write_mode

    def note(*args, **kwargs):
        held.append(True)
        return real(*args, **kwargs)

    monkeypatch.setattr(tqdm.tqdm, "external_write_mode", note)
    _logging.ClickHandler({}).emit(a_record())

    assert held, "wrote without taking the lock"


def test_a_second_console_logger_takes_it_too(monkeypatch):
    # `log_helper.set_console_logger` is public, and a plain StreamHandler
    # would write straight into whatever the dock is drawing.
    was_held = []
    real_emit = logging.StreamHandler.emit

    def note(self, record):
        was_held.append(not lock_is_free())
        real_emit(self, record)

    monkeypatch.setattr(logging.StreamHandler, "emit", note)
    handler = _logging.LockedStreamHandler()
    handler.setStream(FakeStream())
    handler.emit(a_record())

    assert was_held == [True], "wrote without taking the lock"


def test_set_console_logger_installs_the_locking_one():
    _logging.log_helper.set_console_logger()
    try:
        console = [
            h
            for h in _logging.audible_cli_logger.handlers
            if h.get_name() == "ConsoleLogger"
        ]
        assert console, "control: it installed one"
        assert isinstance(console[0], _logging.LockedStreamHandler)
    finally:
        for handler in list(_logging.audible_cli_logger.handlers):
            if handler.get_name() == "ConsoleLogger":
                _logging.audible_cli_logger.removeHandler(handler)


def test_two_writers_cannot_be_inside_at_once():
    order = []

    def slow():
        with _terminal.atomic():
            order.append("first in")
            time.sleep(0.05)
            order.append("first out")

    def other():
        time.sleep(0.01)
        with _terminal.atomic():
            order.append("second in")

    threads = [threading.Thread(target=slow), threading.Thread(target=other)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert order == ["first in", "first out", "second in"], order


def test_the_lock_is_given_back_when_a_write_throws():
    with pytest.raises(RuntimeError), _terminal.atomic():
        raise RuntimeError("the terminal went away")

    assert lock_is_free(), "nobody could write again"


# --- and the dock on the other side of it ----------------------------------


def test_the_dock_writes_inside_the_lock(monkeypatch):
    # Without this the log handler and the dock take turns by luck.
    held = []

    class Watching(FakeStream):
        def isatty(self):
            return True

        def write(self, text):
            held.append(not lock_is_free())
            return super().write(text)

    monkeypatch.setattr(
        progress.shutil, "get_terminal_size", lambda *a: os.terminal_size((80, 24))
    )
    with progress.Dock(2, stream=Watching()) as dock:
        dock.set(0, "a bar")

    assert held, "control: it wrote something"
    assert all(held), "wrote onto the terminal without holding the lock"


def test_a_log_line_waits_for_the_row_being_drawn(monkeypatch):
    # The failure this exists for: a line landing between the cursor move
    # and the text, so the bar is drawn wherever the cursor happened to be.
    seen = []

    class Slow(FakeStream):
        def isatty(self):
            return True

        def write(self, text):
            if "\x1b[" in text:
                seen.append("paint starts")
                time.sleep(0.05)
                seen.append("paint ends")
            return super().write(text)

    def log_from_elsewhere():
        time.sleep(0.01)
        with _terminal.atomic():
            seen.append("log line")

    monkeypatch.setattr(
        progress.shutil, "get_terminal_size", lambda *a: os.terminal_size((80, 24))
    )
    with progress.Dock(1, stream=Slow(), rule=False) as dock:
        seen.clear()
        writer = threading.Thread(target=log_from_elsewhere)
        writer.start()
        dock.set(0, "a bar")
        writer.join()

    first = seen.index("paint starts")
    assert seen[first + 1] == "paint ends", f"a line cut the paint in half: {seen}"
