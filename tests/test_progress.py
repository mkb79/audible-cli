"""The reserved rows the progress bars are drawn into.

What matters here is not how a bar looks but that the terminal is left the
way it was found, that a row is never handed to two downloads at once, and
that everything falls back cleanly where a scroll region is not possible.
"""

import asyncio
import os
import pathlib
import signal

import httpx
import pytest

from audible_cli import progress
from audible_cli.downloader import (
    Downloader,
    DummyProgressBar,
    ResponseInfo,
    get_progressbar,
)


class BigResponse:
    """Enough of an httpx response to make the downloader stream."""

    def __init__(self, content_length):
        self.headers = httpx.Headers({"Content-Length": str(content_length)})
        self.status_code = 200


class FakeTerminal:
    """A stream that claims to be a terminal and records what it is told."""

    def __init__(self, tty=True, with_fileno=False, fd=7):
        self.written = []
        self._tty = tty
        self._with_fileno = with_fileno
        self._fd = fd

    def isatty(self):
        return self._tty

    def write(self, text):
        self.written.append(text)

    def flush(self):
        pass

    def fileno(self):
        if not self._with_fileno:
            raise ValueError("no descriptor")
        return self._fd

    @property
    def text(self):
        return "".join(self.written)


@pytest.fixture
def terminal(monkeypatch):
    """A 24x100 terminal, handed to the dock explicitly.

    Patching `sys.stderr` would not work: pytest reinstalls its own capture
    object after fixture setup, so the dock would see that instead.
    """
    monkeypatch.setattr(
        progress.shutil, "get_terminal_size", lambda *a: os.terminal_size((100, 24))
    )
    return FakeTerminal()


def test_a_pipe_gets_no_dock(terminal):
    assert progress.Dock(4, stream=terminal).enabled, "control: this one works"
    assert not progress.Dock(4, stream=FakeTerminal(tty=False)).enabled


def test_a_terminal_too_short_gets_no_dock(terminal, monkeypatch):
    monkeypatch.setattr(
        progress.shutil,
        "get_terminal_size",
        lambda *a: os.terminal_size((100, progress.MIN_SCROLL_ROWS + 1)),
    )
    # Two rows on top of the minimum that has to stay scrollable is one too
    # many, so the dock declines rather than squeezing the output.
    assert progress.Dock(1, stream=terminal).enabled, "control: one row still fits"
    assert not progress.Dock(2, stream=terminal).enabled


def test_the_region_is_set_and_given_back(terminal):
    dock = progress.Dock(4, stream=terminal)
    assert dock.enabled

    with dock:
        opened = terminal.text
    closed = terminal.text[len(opened) :]

    assert "\x1b[1;20r" in opened, "reserves the four bottom rows of 24"
    assert "\x1b[r" in closed, "hands the whole screen back"
    for row in range(21, 25):
        assert f"\x1b[{row};1H\x1b[2K" in closed, f"row {row} is wiped"


def test_an_exception_still_gives_the_region_back(terminal):
    dock = progress.Dock(2, stream=terminal)
    with pytest.raises(RuntimeError), dock:
        raise RuntimeError("boom")

    assert "\x1b[r" in terminal.text


def test_a_bar_is_drawn_on_its_own_row(terminal):
    with progress.Dock(3, stream=terminal) as dock:
        before = len(terminal.written)
        dock.set(1, "hello")

    painted = "".join(terminal.written[before:])
    assert "\x1b7" in painted and "\x1b8" in painted, "cursor is put back"
    assert "\x1b[23;1H\x1b[2Khello" in painted, "second of three rows at the bottom"


def test_rows_are_handed_out_once_and_returned(terminal):
    with progress.docked_progress(2, stream=terminal):
        first = progress.take_progressbar(pathlib.Path("a"), total=10)
        second = progress.take_progressbar(pathlib.Path("b"), total=10)
        assert first is not None
        assert second is not None
        assert first._row != second._row

        # No third row exists, and the caller has to cope with that.
        assert progress.take_progressbar(pathlib.Path("c"), total=10) is None

        first.close()
        third = progress.take_progressbar(pathlib.Path("d"), total=10)
        assert third is not None
        assert third._row == first._row


def test_closing_twice_does_not_hand_the_row_out_twice(terminal):
    with progress.docked_progress(1, stream=terminal):
        bar = progress.take_progressbar(pathlib.Path("a"), total=10)
        bar.close()
        bar.close()

        assert progress.take_progressbar(pathlib.Path("b"), total=10) is not None
        assert progress.take_progressbar(pathlib.Path("c"), total=10) is None


def test_outside_a_block_there_is_no_docked_bar(terminal):
    assert progress.take_progressbar(pathlib.Path("a"), total=10) is None


def test_the_dock_is_gone_after_the_block(terminal):
    with progress.docked_progress(2, stream=terminal):
        pass

    assert progress.take_progressbar(pathlib.Path("a"), total=10) is None
    assert "\x1b[r" in terminal.text


def test_disabled_means_no_bar_at_all(terminal):
    with progress.docked_progress(4, enabled=False, stream=terminal):
        assert progress.progress_disabled()
        assert progress.take_progressbar(pathlib.Path("a"), total=10) is None
        assert isinstance(
            get_progressbar(pathlib.Path("a"), total=10), DummyProgressBar
        )
        assert "\x1b[" not in terminal.text, "not a single escape sequence"

    assert not progress.progress_disabled()


def test_an_unknown_size_has_nothing_to_show(terminal):
    with progress.docked_progress(2, stream=terminal):
        assert isinstance(
            get_progressbar(pathlib.Path("a"), total=None), DummyProgressBar
        )


def test_a_download_gets_a_docked_bar_when_one_is_free(terminal):
    with progress.docked_progress(2, stream=terminal):
        bar = get_progressbar(pathlib.Path("book.aaxc"), total=1000)
        assert isinstance(bar, progress.DockedProgressBar)

        bar.update(500)
        bar.close()

    assert "book.aaxc" in terminal.text


def test_more_downloads_than_rows_fall_back_to_a_plain_bar(terminal):
    with progress.docked_progress(1, stream=terminal):
        first = get_progressbar(pathlib.Path("a"), total=1000)
        second = get_progressbar(pathlib.Path("b"), total=1000)

        assert isinstance(first, progress.DockedProgressBar)
        assert not isinstance(second, progress.DockedProgressBar)
        # A tqdm bar that leaves nothing behind, not a silent no-op
        assert not isinstance(second, DummyProgressBar)
        assert second.leave is False
        second.close()


# --- the failures a review turned up, kept from coming back ---------------


def test_the_size_comes_from_the_stream_the_bars_go_on(monkeypatch):
    # shutil.get_terminal_size() measures sys.__stdout__. With stdout piped
    # to a file and stderr still a terminal the two differ, and reserving
    # rows of the wrong screen puts the region in the middle of it.
    monkeypatch.setattr(
        progress.shutil, "get_terminal_size", lambda *a: os.terminal_size((80, 24))
    )
    monkeypatch.setattr(
        progress.os, "get_terminal_size", lambda fd: os.terminal_size((50, 10))
    )
    dock = progress.Dock(3, stream=FakeTerminal(with_fileno=True))

    assert dock._measure() == (10, 50), "asks its own terminal, not stdout"


def test_a_dumb_terminal_gets_no_dock(terminal, monkeypatch):
    monkeypatch.setenv("TERM", "xterm")
    assert progress.Dock(2, stream=terminal).enabled, "control: xterm works"

    for term in progress._TERMS_WITHOUT_CURSOR_CONTROL:
        monkeypatch.setenv("TERM", term)
        assert not progress.Dock(2, stream=terminal).enabled, term


def test_an_ignored_signal_is_not_turned_into_a_kill(terminal):
    # Started under nohup, or with a parent that ignored the signal. The
    # dock must hand that on unchanged rather than terminate the process.
    previous = signal.signal(signal.SIGTERM, signal.SIG_IGN)
    try:
        with progress.Dock(2, stream=terminal) as dock:
            assert dock._previous_handlers[signal.SIGTERM] is signal.SIG_IGN
            # Would kill the test process if this were handled wrongly
            dock._on_terminating_signal(signal.SIGTERM, None)

        assert "\x1b[r" in terminal.text, "still gives the region back"
    finally:
        signal.signal(signal.SIGTERM, previous)


def test_a_newer_handler_survives_the_dock(terminal):
    def mine(number, frame):
        pass

    previous = signal.getsignal(signal.SIGWINCH)
    try:
        with progress.Dock(2, stream=terminal):
            signal.signal(signal.SIGWINCH, mine)

        assert signal.getsignal(signal.SIGWINCH) is mine
    finally:
        signal.signal(signal.SIGWINCH, previous)


def test_a_window_shrinking_too_far_hands_out_no_more_rows(terminal, monkeypatch):
    with progress.docked_progress(2, stream=terminal):
        first = progress.take_progressbar(pathlib.Path("a"), total=10)
        assert first is not None

        monkeypatch.setattr(
            progress.shutil,
            "get_terminal_size",
            lambda *a: os.terminal_size((100, progress.MIN_SCROLL_ROWS + 1)),
        )
        progress._current.dock._on_resize(signal.SIGWINCH, None)
        # A repaint is what notices the new size, and a bar throttles its
        # own, so force one rather than wait out the interval.
        first._render(force=True)

        assert not progress._current.dock.available
        # A row nobody can see must not be handed out as if it worked
        assert progress.take_progressbar(pathlib.Path("b"), total=10) is None
        assert "\x1b[r" in terminal.text
        # Out of room is not the end of it: this terminal can still hold a
        # dock, and does again as soon as the window has the space.
        assert progress._current.dock.enabled
        assert progress._current.dock.active


def test_a_bar_outliving_its_dock_keeps_its_hands_off_the_next_one(terminal):
    with progress.docked_progress(1, stream=terminal):
        stale = progress.take_progressbar(pathlib.Path("a"), total=10)

    with progress.docked_progress(1, stream=FakeTerminal()):
        live = progress.take_progressbar(pathlib.Path("b"), total=10)
        assert live is not None

        stale.close()  # belongs to the dock that is already gone

        assert progress.take_progressbar(pathlib.Path("c"), total=10) is None, (
            "row 0 is still the live bar's"
        )


# --- a second review round: the fixes, and the tests that were green-blind ---


def test_a_disabled_bar_survives_being_released(tmp_path):
    # `--no-progress` hands back a bar that shows nothing, and the download
    # releases every bar when it is done. That release used to raise
    # AttributeError and take the temporary-file cleanup down with it.
    dl = Downloader(
        source=httpx.URL("https://example.invalid/book.aaxc"),
        client=None,
        expected_types=[],
    )
    head = ResponseInfo(BigResponse(Downloader.MIN_STREAM_LENGTH))

    async def fake_head(force_recreate=False):
        return head

    async def fake_stream(**kwargs):
        raise RuntimeError("connection lost")

    handled = []

    async def fake_handle_tmp_file(**kwargs):
        handled.append(kwargs)

    dl.get_head_response = fake_head
    dl._stream_download = fake_stream
    dl._handle_tmp_file = fake_handle_tmp_file

    with progress.docked_progress(4, enabled=False), pytest.raises(RuntimeError):
        asyncio.run(dl.run(target=tmp_path / "book.aaxc", force_reload=True))

    # Releasing the bar comes first in that `finally`, so a crash there used
    # to take the temporary-file cleanup with it.
    assert handled, "the temporary file is still dealt with"


def test_a_failed_download_gives_its_row_back(tmp_path, terminal):
    # The bar's own `with` only covers a stream that opened. Failing before
    # that used to keep the row for good.
    dl = Downloader(
        source=httpx.URL("https://example.invalid/book.aaxc"),
        client=None,
        expected_types=[],
    )
    head = ResponseInfo(BigResponse(Downloader.MIN_STREAM_LENGTH))

    async def fake_head(force_recreate=False):
        return head

    async def fake_stream(**kwargs):
        raise RuntimeError("connection lost")

    dl.get_head_response = fake_head
    dl._stream_download = fake_stream

    with progress.docked_progress(1, stream=terminal):
        with pytest.raises(RuntimeError):
            asyncio.run(dl.run(target=tmp_path / "book.aaxc", force_reload=True))

        assert progress.take_progressbar(pathlib.Path("next"), total=10) is not None


def test_a_resize_that_fits_moves_the_region_and_repaints(terminal, monkeypatch):
    with progress.docked_progress(2, stream=terminal) as _:
        bar = progress.take_progressbar(pathlib.Path("a"), total=1000)
        bar.update(100)
        monkeypatch.setattr(
            progress.shutil, "get_terminal_size", lambda *a: os.terminal_size((100, 18))
        )
        progress._current.dock._on_resize(signal.SIGWINCH, None)
        before = len(terminal.written)
        bar._render(force=True)
        # Read it here: after the block, the cleanup has written to these
        # rows too and would satisfy the assertions on its own.
        after = "".join(terminal.written[before:])

    region = after.index("\x1b[1;16r")
    assert region < after.index("\x1b[16;1H"), (
        "the region homes the cursor, so CUP after"
    )
    assert "\x1b[17;1H" in after, "the bar is repainted at the new place"
    assert progress._current.dock is None


def test_the_cursor_is_saved_around_the_absolute_move(terminal):
    with progress.Dock(3, stream=terminal) as dock:
        before = len(terminal.written)
        dock.set(0, "hello")

    painted = "".join(terminal.written[before:])
    save, move, restore = (
        painted.index("\x1b7"),
        painted.index("\x1b[22;1H"),
        painted.index("\x1b8"),
    )
    assert save < move < restore, "saving after the move would restore the wrong place"


def test_the_size_is_read_from_the_right_descriptor(monkeypatch):
    asked = []

    def fake(fd):
        asked.append(fd)
        return os.terminal_size((50, 10))

    monkeypatch.setattr(progress.os, "get_terminal_size", fake)
    # Not descriptor 2: a hard-coded stderr would pass that
    stream = FakeTerminal(with_fileno=True, fd=7)
    progress.Dock(3, stream=stream)._measure()

    assert asked, "asks somebody"
    assert set(asked) == {7}, "asks the stream the bars go on"


def test_a_short_write_is_finished_off(monkeypatch):
    written = []

    def stingy(fd, data):
        written.append(data)
        return 1  # one byte at a time, as a pipe may do

    monkeypatch.setattr(progress.os, "write", stingy)
    dock = progress.Dock(2, stream=FakeTerminal(with_fileno=True))
    dock._write_raw("abc")

    assert [bytes(d) for d in written] == [b"abc", b"bc", b"c"]


def test_a_signal_before_the_region_exists_wipes_no_rows(terminal):
    # The window between counting as open and actually setting the region.
    # `_top` is still zero there, so a restore that does not know the region
    # was never set would wipe the top rows of the terminal instead.
    dock = progress.Dock(3, stream=terminal)
    real_measure = dock._measure

    def measure_and_get_signalled():
        size = real_measure()
        dock._on_terminating_signal(signal.SIGTERM, None)  # ignored, returns
        return size

    dock._measure = measure_and_get_signalled
    previous = signal.signal(signal.SIGTERM, signal.SIG_IGN)
    try:
        with dock:
            pass
    finally:
        signal.signal(signal.SIGTERM, previous)

    assert "\x1b[1;1H\x1b[2K" not in terminal.text, "must not wipe the first row"
    assert "\x1b[2;1H\x1b[2K" not in terminal.text, "nor the second"
    assert not dock.active


def test_a_signal_mid_repaint_stops_the_repaint(terminal):
    with progress.Dock(2, stream=terminal) as dock:
        dock.set(0, "one")
        dock.set(1, "two")
        dock._resized = True
        real_paint = dock._paint

        def paint_then_get_signalled(row, text):
            real_paint(row, text)
            dock._active = False  # as a surviving signal would leave it

        dock._paint = paint_then_get_signalled
        before = len(terminal.written)
        dock.set(0, "three")
        # Count what reached the terminal, not what was called: the guard
        # sits inside `_paint`, so a call proves nothing.
        during = "".join(terminal.written[before:])

    assert "three" in during, "the row being set is still painted"
    assert "two" not in during, "the next row is not put onto a released screen"


def test_a_bar_is_refused_while_the_dock_is_closing(terminal):
    with progress.docked_progress(1, stream=terminal):
        dock = progress._current.dock
        seen = []

        real_write = dock._write

        def write_and_look(text):
            if "\x1b[r" in text:
                # Mid-restore: the dock is on its way out but still current
                seen.append(
                    (
                        progress._current.dock is dock,
                        progress.take_progressbar(pathlib.Path("x"), total=10),
                    )
                )
            real_write(text)

        dock._write = write_and_look

    # Still the current dock, so the refusal has to come from its own state
    # rather than from the slot already being empty
    assert seen == [(True, None)], "no row once the region is going back"


def test_a_signal_while_the_region_is_being_set_still_gives_it_back(terminal):
    # The window between the terminal actually having a region and the dock
    # knowing it. A default disposition kills the process right there, so
    # whatever the handler writes is the last chance to release it.
    dock = progress.Dock(3, stream=terminal)
    real_write = dock._write

    def write_then_get_signalled(text):
        if "\x1b[1;21r" in text:
            # Before the bytes go out: the dock has recorded the region but
            # the terminal has not got it yet. Firing afterwards would pass
            # even with the flag set after the write.
            dock._on_terminating_signal(signal.SIGTERM, None)
        real_write(text)

    dock._write = write_then_get_signalled
    previous = signal.signal(signal.SIGTERM, signal.SIG_IGN)
    try:
        with dock:
            pass
    finally:
        signal.signal(signal.SIGTERM, previous)

    assert not dock.active
    # Whatever the order the two ended up in, the last word is a release
    assert terminal.text.rindex("\x1b[r") > terminal.text.index("\x1b[1;21r"), (
        "the reservation that landed after the restore is taken back again"
    )


def test_a_row_is_painted_in_one_write(terminal):
    # A signal runs between bytecodes. Split over several writes, a paint
    # can be cut in half by one, and the rest would land after the restore.
    with progress.Dock(2, stream=terminal) as dock:
        before = len(terminal.written)
        dock.set(0, "hello")
        writes = len(terminal.written) - before

    assert writes == 1, f"one paint, one write, got {writes}"


def test_every_signal_that_would_kill_us_is_caught(terminal):
    # Python runs no `atexit` for a terminating signal nobody handled, so a
    # signal missing from this set takes the scroll region to the grave.
    # Ctrl-\ was the one that got away.
    assert {"SIGTERM", "SIGHUP", "SIGQUIT"} <= set(progress._CAUGHT_SIGNALS)

    with progress.Dock(2, stream=terminal) as dock:
        for name in progress._CAUGHT_SIGNALS:
            installed = signal.getsignal(getattr(signal, name))
            assert installed == dock._on_terminating_signal, name


def test_a_paint_that_lands_after_a_restore_is_taken_back(terminal):
    # A handler that returns restores while our write is on its way, so
    # the bytes arrive on a screen with no region any more.
    # Ignore it *before* the dock opens, so the dock records SIG_IGN as the
    # previous disposition. Setting it afterwards leaves the dock holding
    # SIG_DFL, and the handler would dutifully kill the test runner.
    previous = signal.signal(signal.SIGTERM, signal.SIG_IGN)
    try:
        with progress.docked_progress(1, stream=terminal):
            dock = progress._current.dock
            bar = progress.take_progressbar(pathlib.Path("a"), total=100)

            def restore_then_let_the_write_land(row, text):
                dock._on_terminating_signal(signal.SIGTERM, None)
                dock._write(f"\x1b[{dock._top + row};1Hstale")

            dock._paint = restore_then_let_the_write_land
            bar._render(force=True)
    finally:
        signal.signal(signal.SIGTERM, previous)

    assert "stale" in terminal.text, "the test has to actually get a write in"
    assert terminal.text.rindex("\x1b[r") > terminal.text.rindex("stale"), (
        "the last word belongs to the release, not to the late paint"
    )


# --- numbered slots and the running total ---------------------------------


def test_every_slot_shows_its_number_from_the_start(terminal):
    with progress.docked_progress(3, stream=terminal):
        painted = terminal.text

    for number in ("1.", "2.", "3."):
        assert number in painted, number


def test_a_slot_keeps_its_number_while_it_waits(terminal):
    with progress.docked_progress(2, stream=terminal):
        first = progress.take_progressbar(pathlib.Path("a"), total=10)
        assert first._description.startswith("1. ")
        before = len(terminal.written)
        top = progress._current.dock._top
        first.close()

    # The line goes back to the bare number, not to nothing: an idle slot
    # has to keep reading as that slot.
    after = "".join(terminal.written[before:])
    assert f"\x1b[{top + first._row};1H\x1b[2K1." in after, after[:200]


def test_the_numbers_line_up_past_nine(terminal):
    with progress.docked_progress(12, stream=terminal):
        bar = progress.take_progressbar(pathlib.Path("a"), total=10)
        assert bar._description.startswith(" 1. "), bar._description


def test_the_running_total_sits_below_the_slots_and_counts(terminal):
    with progress.docked_progress(2, stream=terminal, total=5):
        before = len(terminal.written)
        progress.advance_summary()
        progress.advance_summary(2)
        painted = "".join(terminal.written[before:])

    # Two slots, so the total is the third reserved row of a 24 line screen
    assert "\x1b[24;1H" in painted, "the last reserved line"
    assert "3/5" in painted, painted[-160:]


def test_without_a_total_no_line_is_given_to_one(terminal):
    with progress.docked_progress(2, stream=terminal):
        progress.advance_summary()  # nothing to count, must not raise
        painted = terminal.text

    assert "Overall" not in painted
    assert "\x1b[1;22r" in painted, "two reserved rows, not three"


def test_the_summary_does_not_outlive_its_block(terminal):
    # It used to stay behind: a later block without a total kept advancing
    # the old one, and it held its dock and stream for the whole process.
    with progress.docked_progress(1, stream=terminal, total=2):
        progress.advance_summary()

    assert progress._current.summary is None
    assert progress._current.labels == []

    progress.advance_summary()  # nothing to count, must not raise

    with progress.docked_progress(1, stream=FakeTerminal()):
        progress.advance_summary()
        assert progress._current.summary is None


def test_work_discovered_later_grows_the_total(terminal):
    # A book that comes in parts queues one job per part while the queue is
    # already draining. The total taken at the start is then short, and the
    # count would run past its own denominator.
    with progress.docked_progress(1, stream=terminal, total=2):
        before = len(terminal.written)
        progress.grow_summary_total(3)
        for _ in range(5):
            progress.advance_summary()
        painted = "".join(terminal.written[before:])

    assert "5/5" in painted, painted[-200:]
    assert "5/2" not in painted


def test_growing_before_there_is_a_block_is_ignored(terminal):
    # Jobs queued up front are already in the count taken when the block
    # opens; counting them again would double them.
    progress.grow_summary_total(4)

    with progress.docked_progress(1, stream=terminal, total=2):
        before = len(terminal.written)
        progress.advance_summary()
        painted = "".join(terminal.written[before:])

    assert "1/2" in painted, painted[-200:]


# --- a phone turned sideways ----------------------------------------------


def test_a_dock_that_would_eat_the_window_is_refused(monkeypatch):
    # Eight jobs need nine lines. That is fine on a full screen and absurd
    # on a phone in landscape, where it would leave five lines for all the
    # output the dock exists to make room for.
    def screen(rows):
        monkeypatch.setattr(
            progress.shutil,
            "get_terminal_size",
            lambda *a: os.terminal_size((80, rows)),
        )

    screen(24)
    assert progress.Dock(9, stream=FakeTerminal()).enabled, "fits on a full screen"

    screen(14)
    assert not progress.Dock(9, stream=FakeTerminal()).enabled, "not on a phone"

    screen(18)
    assert progress.Dock(9, stream=FakeTerminal()).enabled, "exactly half is allowed"


def test_a_resize_releases_the_region_at_once(monkeypatch):
    # Not on the next repaint: a run waiting on the network can go minutes
    # without one, and until then the terminal keeps scrolling by margins
    # that no longer match the window. A stream with a descriptor, so the
    # signal path really goes past the buffering as it does in production.
    raw = []
    monkeypatch.setattr(
        progress.os, "get_terminal_size", lambda fd: os.terminal_size((100, 24))
    )
    monkeypatch.setattr(
        progress.os, "write", lambda fd, data: (raw.append(data), len(data))[1]
    )
    screen = FakeTerminal(with_fileno=True)

    with progress.docked_progress(2, stream=screen, total=4):
        dock = progress._current.dock
        dock._on_resize(signal.SIGWINCH, None)

        assert not dock._reserved
        released = b"".join(raw).decode()

    # Releasing the margins homes the cursor exactly as setting them does,
    # so the next ordinary line would start at the top of the screen and
    # write over everything up there.
    assert released == "\x1b7\x1b[r\x1b8", f"got {released!r}"


def test_a_height_change_wipes_nothing_by_its_old_row_numbers(terminal, monkeypatch):
    # Where the old rows went is not knowable: a terminal rewraps its lines
    # when the width changes. Erasing them by their old numbers could take
    # the user's output with it, and a stale bar is the cheaper mistake.
    with progress.docked_progress(2, stream=terminal):
        bar = progress.take_progressbar(pathlib.Path("a"), total=1000)
        bar.update(100)
        monkeypatch.setattr(
            progress.shutil,
            "get_terminal_size",
            lambda *a: os.terminal_size((100, 18)),
        )
        before = len(terminal.written)
        progress._current.dock._on_resize(signal.SIGWINCH, None)
        bar._render(force=True)
        during = "".join(terminal.written[before:])

    upto_new_region = during[: during.index("\x1b[1;16r")]
    assert "\x1b[2K" not in upto_new_region, f"erased something: {upto_new_region!r}"


def test_a_width_change_moves_the_dock_rather_than_ending_it(terminal, monkeypatch):
    # Turning a phone sideways changes the width, so this is the ordinary
    # case there, not the exception. Where the old rows went is unknowable,
    # but the new bottom is not, and every row can say what it holds.
    with progress.docked_progress(2, stream=terminal):
        dock = progress._current.dock
        bar = progress.take_progressbar(pathlib.Path("a"), total=10)
        assert bar is not None

        monkeypatch.setattr(
            progress.shutil,
            "get_terminal_size",
            lambda *a: os.terminal_size((60, 18)),
        )
        dock._on_resize(signal.SIGWINCH, None)
        before = len(terminal.written)
        dock.set(0, "anything")  # the repaint notices the new width
        after = "".join(terminal.written[before:])

        assert dock.available
        assert dock.enabled
        assert "\x1b[1;16r" in after, "a region for the window it is now"
        assert "\x1b[17;1H" in after, "and the rows painted where it put them"
        assert dock.width == 60, "the bars are told the width they render for"
        assert progress.take_progressbar(pathlib.Path("b"), total=10) is not None


def test_a_bar_keeps_drawing_after_a_width_change(terminal, monkeypatch):
    # What the user sees: bars that go quiet and then drift up with the
    # output are the whole complaint. An update after the turn has to land.
    with progress.docked_progress(2, stream=terminal):
        bar = progress.take_progressbar(pathlib.Path("a"), total=1000)
        bar.update(100)
        monkeypatch.setattr(
            progress.shutil, "get_terminal_size", lambda *a: os.terminal_size((60, 18))
        )
        progress._current.dock._on_resize(signal.SIGWINCH, None)
        bar._render(force=True)  # works the resize out and rebuilds

        before = len(terminal.written)
        bar.update(100)
        bar._render(force=True)
        after = "".join(terminal.written[before:])

    assert "\x1b[17;1H" in after, f"the bar went quiet: {after!r}"


def test_a_width_change_wipes_nothing_by_its_old_row_numbers(terminal, monkeypatch):
    # The dangerous case: after a rewrap the old rows may hold the user's
    # output, so erasing them by number would take that with it. The rows of
    # the new dock are ours and may be cleared; rows 23 and 24 are not.
    with progress.docked_progress(2, stream=terminal):
        bar = progress.take_progressbar(pathlib.Path("a"), total=1000)
        bar.update(100)
        monkeypatch.setattr(
            progress.shutil,
            "get_terminal_size",
            lambda *a: os.terminal_size((60, 18)),
        )
        before = len(terminal.written)
        progress._current.dock._on_resize(signal.SIGWINCH, None)
        bar._render(force=True)
        during = "".join(terminal.written[before:])

    for old_row in (23, 24):
        assert f"\x1b[{old_row};1H\x1b[2K" not in during, (
            f"erased row {old_row}, which is the user's now: {during!r}"
        )
    assert "\x1b[17;1H\x1b[2K" in during, "its own new rows it may clear"


def test_a_window_dragged_out_and_back_rebuilds_once(terminal, monkeypatch):
    # Two reflows before anything repaints. The width it ends on is the one
    # it started with, so nothing distinguishes this from no resize at all
    # except the latch the handler sets -- and one rebuild settles both.
    with progress.docked_progress(2, stream=terminal):
        dock = progress._current.dock
        monkeypatch.setattr(
            progress.shutil, "get_terminal_size", lambda *a: os.terminal_size((60, 24))
        )
        dock._on_resize(signal.SIGWINCH, None)  # away
        monkeypatch.setattr(
            progress.shutil, "get_terminal_size", lambda *a: os.terminal_size((100, 24))
        )
        dock._on_resize(signal.SIGWINCH, None)  # and back
        dock.set(0, "anything")

        assert dock.available
        assert not dock._rewrapped, "worked out, so the exit may erase again"
        assert not dock._resized


def test_a_bar_asked_for_after_a_resize_is_never_a_mute_one(terminal, monkeypatch):
    # The real order on a rotated phone: the window changes, and the next
    # thing that happens is a new download asking for a row -- before any
    # existing bar has painted and noticed. `settle` works the resize out
    # first, so the row it hands back is one of the new ones.
    with progress.docked_progress(2, stream=terminal):
        monkeypatch.setattr(
            progress.shutil, "get_terminal_size", lambda *a: os.terminal_size((60, 18))
        )
        progress._current.dock._on_resize(signal.SIGWINCH, None)

        before = len(terminal.written)
        assert progress.take_progressbar(pathlib.Path("a"), total=10) is not None
        assert "\x1b[1;16r" in "".join(terminal.written[before:]), (
            "settled before the row was handed out"
        )


def test_a_resize_stops_the_exit_from_erasing_old_rows(terminal, monkeypatch):
    # SIGWINCH and SIGTERM can arrive in either order. If the terminal has
    # rewrapped, the rows the cleanup would erase may hold the user's
    # output, so once a resize has been seen it only gives the margins back.
    with progress.docked_progress(2, stream=terminal) as _:
        dock = progress._current.dock
        monkeypatch.setattr(
            progress.shutil, "get_terminal_size", lambda *a: os.terminal_size((60, 24))
        )
        # Mid-paint, so the handler leaves the release to the repaint and
        # the region is still ours when the block ends. Otherwise the exit
        # writes nothing at all and the test proves nothing.
        dock._painting = True
        dock._on_resize(signal.SIGWINCH, None)
        dock._painting = False
        assert dock._reserved, "the test needs the region to still be held"
        before = len(terminal.written)

    leaving = "".join(terminal.written[before:])
    assert "\x1b[r" in leaving, "the margins still come back"
    assert "\x1b[2K" not in leaving, f"erased rows on the way out: {leaving!r}"


def test_a_signal_between_marking_and_writing_does_not_strand_the_region(terminal):
    # The window where `_reserve` has said the region exists but has not
    # written it yet. A resize in there released nothing, and the write
    # then installed margins that nobody knew about — so nothing gave them
    # back, not even at exit.
    dock = progress.Dock(2, stream=terminal)
    real_write = dock._write

    def write_then_get_resized(text):
        if "\x1b[1;22r" in text:
            # Exactly as the handler leaves it: the flag set *and* the
            # reservation given up. Without the second half the exit still
            # rescues us and the test proves nothing.
            dock._resized = True
            dock._reserved = False
        real_write(text)

    dock._write = write_then_get_resized
    with dock:
        pass

    assert not dock._reserved
    assert terminal.text.rindex("\x1b[r") > terminal.text.index("\x1b[1;22r")


def test_the_handler_keeps_its_hands_off_a_paint_in_progress(terminal, monkeypatch):
    # A terminal keeps one saved cursor. Saving here would hand the paint's
    # own restore the wrong place to go back to, and ordinary output would
    # carry on inside the reserved rows.
    raw = []
    monkeypatch.setattr(
        progress.os, "write", lambda fd, data: (raw.append(data), len(data))[1]
    )
    with progress.docked_progress(2, stream=terminal):
        dock = progress._current.dock
        dock._painting = True
        dock._on_resize(signal.SIGWINCH, None)

        assert raw == [], "wrote over the paint's saved cursor"
        assert dock._reserved, "the release waits for the repaint instead"


def test_nothing_is_painted_once_a_resize_is_pending(terminal):
    # `set` looks at the flag, but a signal runs between two bytecodes and
    # can set it afterwards, or between two rows of a repaint.
    with progress.docked_progress(2, stream=terminal):
        dock = progress._current.dock
        dock._resized = True
        before = len(terminal.written)
        dock._paint(0, "should not appear")

    assert "should not appear" not in "".join(terminal.written[before:])


def test_a_dock_out_of_room_comes_back_when_the_room_does(terminal, monkeypatch):
    # Landscape on a phone is short: nine rows do not fit, and the old dock
    # took that as final. Turning back gives the room again, and a dock that
    # stayed out for good is the same bug from the other side.
    with progress.docked_progress(8, stream=terminal, total=8):
        dock = progress._current.dock  # 8 workers plus the summary
        bar = progress.take_progressbar(pathlib.Path("a"), total=1000)
        assert bar is not None

        monkeypatch.setattr(
            progress.shutil, "get_terminal_size", lambda *a: os.terminal_size((90, 14))
        )
        dock._on_resize(signal.SIGWINCH, None)
        bar._render(force=True)
        assert not dock.available, "control: 14 rows have no room for 9"
        assert dock.enabled, "out of room is not the same as unusable"

        monkeypatch.setattr(
            progress.shutil, "get_terminal_size", lambda *a: os.terminal_size((45, 40))
        )
        dock._on_resize(signal.SIGWINCH, None)
        before = len(terminal.written)
        bar._render(force=True)
        after = "".join(terminal.written[before:])

        assert dock.available, "the room came back and the dock did not"
        assert "\x1b[1;31r" in after, f"no region for this window: {after!r}"
        assert "\x1b[32;1H" in after, "the first row is drawn again"


def test_nothing_is_painted_while_there_is_no_room(terminal, monkeypatch):
    with progress.docked_progress(2, stream=terminal):
        dock = progress._current.dock
        monkeypatch.setattr(
            progress.shutil,
            "get_terminal_size",
            lambda *a: os.terminal_size((100, progress.MIN_SCROLL_ROWS + 1)),
        )
        dock._on_resize(signal.SIGWINCH, None)
        dock.set(0, "settles into the pause")

        before = len(terminal.written)
        dock.set(0, "and this one has nowhere to go")
        assert "".join(terminal.written[before:]) == "", "drew into a window too small"

    # Kept all the same, so the row can show it once the room is back
    assert dock._lines[0] == "and this one has nowhere to go"


def test_a_resize_during_the_rebuild_leaves_no_stale_region(terminal, monkeypatch):
    # The margins being written describe the window as it was measured. A
    # turn landing in between makes them wrong before they arrive, and the
    # handler cannot take them back: it ran before they were written.
    monkeypatch.setattr(
        progress.shutil, "get_terminal_size", lambda *a: os.terminal_size((100, 24))
    )
    dock = progress.Dock(2, stream=terminal)
    with dock:
        monkeypatch.setattr(
            progress.shutil, "get_terminal_size", lambda *a: os.terminal_size((60, 18))
        )
        dock._on_resize(signal.SIGWINCH, None)

        real_write = dock._write

        def write_then_turn_again(text):
            if "\x1b[1;16r" in text:
                monkeypatch.setattr(
                    progress.shutil,
                    "get_terminal_size",
                    lambda *a: os.terminal_size((100, 30)),
                )
                dock._on_resize(signal.SIGWINCH, None)
            real_write(text)

        dock._write = write_then_turn_again
        dock.set(0, "anything")
        dock._write = real_write

        assert dock._resized, "the second turn is still owed an answer"
        assert not dock._reserved, "margins for a window that is already gone"
        assert terminal.text.rindex("\x1b[r") > terminal.text.rindex("\x1b[1;16r"), (
            "the stale region is given back, not left set"
        )

        # And the next paint settles it on the geometry that is real now
        before = len(terminal.written)
        dock.set(0, "anything")
        after = "".join(terminal.written[before:])

        assert dock.available

    assert "\x1b[1;28r" in after, f"never caught up: {after!r}"
