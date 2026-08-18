"""Progress bars that hold their line.

A tqdm bar addresses its line relative to the cursor, so any output printed
during a download shifts the whole block and concurrent bars overwrite one
another. Instead the terminal is told to scroll only the region *above* a
few reserved rows, which are then addressed by absolute row. tqdm still
formats the meter; the count, the cadence and the placement are ours.

A scroll region is terminal state and outlives the process that set it.
Everything catchable puts it back, `atexit` included; SIGKILL, `os._exit()`
and a fatal signal do not, and restoring means writing to a terminal that
may be blocked, so it is best effort rather than a guarantee.

The dock steps aside rather than fight: no terminal, one without cursor
control, or a Windows console that refuses escape sequences. A window too
short for the rows only pauses it. A resize rebuilds it at the bottom of
whatever the window is now; where the old rows went is unknowable, so
nothing is erased by their numbers. While it stands aside, callers get a
plain tqdm bar.
"""

from __future__ import annotations

import atexit
import contextlib
import os
import pathlib
import shutil
import signal
import sys
import threading
import time
from collections.abc import Callable, Iterator
from types import FrameType
from typing import IO, Any

import click
import tqdm

from ._terminal import atomic


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes


#: Never repaint a single bar more often than this, in seconds. Without it a
#: fast local disk repaints on every chunk and floods the terminal.
MIN_REPAINT_INTERVAL = 0.1

#: Drawn across the top of the dock, so the rows read as a block rather
#: than as the last few log lines. Costs a row; the other three sides would
#: cost a second row and two columns of a screen already short of both.
RULE = "\u2500"

#: Dropped first when the window is narrow: the clock costs about twenty
#: columns, and knowing which download a row is beats knowing its rate.
COMPACT_METER = "{desc}{percentage:3.0f}%|{bar}| {n_fmt}/{total_fmt}"

#: What the running total calls itself.
SUMMARY_PREFIX = "Overall"

#: Below this much room for the name, the clock goes rather than the name.
MIN_NAME_COLUMNS = 12

#: Columns the bar keeps for itself. Below this tqdm drops the percentage,
#: the counts and the bar in that order, leaving a cut-off name and no
#: progress at all -- which on a phone is most of the line.
MIN_BAR_COLUMNS = 8

# : Rows that have to stay scrollable. The scrolling part also keeps
# : half the window, or eight jobs would swallow a phone screen.
MIN_SCROLL_ROWS = 4

#: Terminals that tell us they cannot place a cursor. An empty TERM counts:
#: something is a terminal but will not say what, so assume the least.
_TERMS_WITHOUT_CURSOR_CONTROL = ("", "dumb", "unknown")

# Catchable signals that end the process. Python runs no `atexit` for
# an unhandled one, so anything missing here takes the region with it.
# SIGINT is absent on purpose: it unwinds as `KeyboardInterrupt`.
_CAUGHT_SIGNALS = [
    s for s in ("SIGTERM", "SIGHUP", "SIGQUIT", "SIGBREAK") if hasattr(signal, s)
]

_ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004


def _windows_console_mode(fd: int | None) -> tuple[Any, int] | None:
    """The console handle for `fd` and its current mode, or None.

    By descriptor, because a wrapper around the same console is a different
    object; anything but 1 or 2 has no standard handle and gets no dock.
    """
    if fd not in (1, 2):
        return None

    try:
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        # Without these the pointer-sized HANDLE comes back through a C int
        # and is truncated on 64-bit Windows.
        kernel32.GetStdHandle.restype = wintypes.HANDLE
        kernel32.GetStdHandle.argtypes = [wintypes.DWORD]
        kernel32.GetConsoleMode.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.DWORD),
        ]
        kernel32.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]

        std_output, std_error = -11, -12
        handle = kernel32.GetStdHandle(std_output if fd == 1 else std_error)
        mode = wintypes.DWORD()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return None
    except Exception:
        return None

    return handle, mode.value


def _set_windows_console_mode(handle: Any, mode: int) -> bool:
    try:
        return bool(ctypes.windll.kernel32.SetConsoleMode(handle, mode))  # type: ignore[attr-defined]
    except Exception:
        return False


class Dock:
    """Rows reserved at the bottom of the terminal, addressed absolutely."""

    def __init__(
        self,
        rows: int,
        stream: IO[str] | None = None,
        keep_row: int | None = None,
        rule: bool = True,
    ) -> None:
        self._stream = stream if stream is not None else sys.stderr
        self._rows = rows
        # The one row worth keeping when there is no room for all of them.
        self._keep_row = keep_row
        self._rule = rule
        # Row numbers on screen, top to bottom. Fewer than all of them when
        # the window is short, so `_top + n` is a position, not a row.
        self._shown = list(range(rows))
        self._lines = [""] * rows
        self._lock = threading.RLock()
        self._active = False
        self._reserved = False
        self._resized = False
        self._previous_handlers: dict[int, Any] = {}
        self._renderers: dict[int, Callable[[], str]] = {}
        self._windows_console: tuple[Any, int] | None = None
        self._top = 0
        self._width = 80
        self._reserved_width = 0
        self._reserved_height = 0
        self._rewrapped = False
        self._painting = False
        self._paused = False
        self._fd = self._descriptor()
        self.enabled = self._usable()

    def settle(self) -> None:
        """Work out a pending resize now rather than at the next paint.

        A row handed out while one is outstanding may not be ours by the
        time anything is drawn on it.
        """
        with self._lock:
            if self._resized and self._active:
                self._reopen_after_resize()

    @property
    def active(self) -> bool:
        """Whether the dock is still open.

        Not whether the region is set: this goes false first on the way out.
        """
        return self._active

    @property
    def available(self) -> bool:
        """Whether anything at all is being drawn.

        False while the window is too small for even the one kept row. The
        dock stays open through that and comes back with the room.
        """
        return self._active and not self._paused

    @property
    def showing_all(self) -> bool:
        """Whether every row has a place, so one may be handed out."""
        return self.available and len(self._shown) == self._rows

    def _usable(self) -> bool:
        if self._rows <= 0:
            return False
        try:
            if not self._stream.isatty():
                return False
        except (AttributeError, ValueError):
            return False

        if not self._takes_escape_sequences():
            return False

        return self._choose_layout(self._measure()[0]) is not None

    def _fits(self, height: int, count: int) -> bool:
        """Whether reserving `count` rows still leaves the window usable."""
        return height - count >= max(MIN_SCROLL_ROWS, height // 2)

    def _layouts(self) -> list[list[int]]:
        """Row sets to try, widest first.

        Whatever fits beats nothing: on a phone with the keyboard up there
        is no room for eight bars, but there is room for three, and the
        running total is the last thing to go.
        """
        everything = list(range(self._rows))
        if self._keep_row is None or self._rows <= 1:
            return [everything]
        rest = [row for row in everything if row != self._keep_row]
        return [everything] + [
            sorted([*rest[:keep], self._keep_row])
            for keep in range(len(rest) - 1, -1, -1)
        ]

    def _choose_layout(self, height: int) -> list[int] | None:
        for shown in self._layouts():
            if self._fits(height, len(shown) + self._rule):
                return shown
        return None

    @property
    def _reserved_rows(self) -> int:
        """Lines the dock holds, the rule above them included."""
        return len(self._shown) + self._rule

    def _line_of(self, position: int) -> int:
        """Screen line of the `position`-th row on show."""
        return self._top + self._rule + position

    def _takes_escape_sequences(self) -> bool:
        """Whether this terminal can be told where to put the cursor."""
        if sys.platform != "win32":
            return os.environ.get("TERM", "") not in _TERMS_WITHOUT_CURSOR_CONTROL

        # Try it and put the mode straight back: the dock may still turn out
        # unusable, and a console left in a mode nobody restores is worse
        # than no dock.
        console = _windows_console_mode(self._fd)
        if console is None:
            return False
        handle, mode = console
        if not _set_windows_console_mode(
            handle, mode | _ENABLE_VIRTUAL_TERMINAL_PROCESSING
        ):
            return False
        _set_windows_console_mode(handle, mode)
        return True

    def _descriptor(self) -> int | None:
        """The raw file descriptor, for writing from a signal handler."""
        try:
            return self._stream.fileno()
        except (AttributeError, ValueError, OSError):
            return None

    def _measured_width(self) -> int:
        try:
            return self._measure()[1]
        except OSError:
            return self._reserved_width

    def _measure(self) -> tuple[int, int]:
        """The size of the terminal the bars go on, as (rows, columns).

        Not `shutil.get_terminal_size()`, which measures `sys.__stdout__`:
        with stdout redirected the two are different screens.
        """
        if self._fd is not None:
            try:
                size = os.get_terminal_size(self._fd)
            except OSError:
                pass
            else:
                return size.lines, size.columns

        size = shutil.get_terminal_size()
        return size.lines, size.columns

    # -- lifetime ---------------------------------------------------------

    def __enter__(self) -> Dock:
        if self.enabled:
            try:
                self._open()
            except BaseException:
                # A KeyboardInterrupt here would skip `__exit__` entirely,
                # and the region would sit there until the process ends.
                self._close()
                raise
        return self

    def __exit__(self, *exc: object) -> bool:
        self._close()
        return False

    def _open(self) -> None:
        with self._lock:
            # Handlers first, so a signal arriving during the measuring finds a
            # dock that can clean up, and a resize in there is noticed.
            self._active = True
            self._install_handlers()
            atexit.register(self._close)
            if not self._enable_windows_escapes():
                self._active = False
                return

            height, self._width = self._measure()
            self._reserved_width = self._width
            self._reserved_height = height
            shown = self._choose_layout(height)
            if shown is None:
                self._paused = True
                return
            self._shown = shown
            self._top = height - self._reserved_rows + 1
            # Scroll the reserved rows into existence, so nothing already on
            # screen ends up underneath them.
            self._scroll_room_into_being(height)
            self._reserve(height)
            self._paint_rule()
            self._flush()

    def _reserve(self, height: int, after_resize: bool = False) -> None:
        """Set the scroll region, and take it back if we are already out.

        A signal that restores and lets the process live on would otherwise
        leave it set with nobody to release it.
        """
        if not self._active or (self._resized and not after_resize):
            return

        # Marked before written: a signal in between has to find something to
        # give back, and releasing a region that was never set costs nothing.
        # The other order loses it for good.
        self._reserved = True
        # One write, for the same reason as in `_paint`: a sequence spread
        # over several calls is the easy one for a handler to cut in half.
        self._write_atomic(
            f"\x1b[1;{height - self._reserved_rows}r"
            # Setting the region homes the cursor, so put it back into the
            # scrolling part before ordinary output continues there.
            f"\x1b[{height - self._reserved_rows};1H"
        )

        if not self._active:
            # A signal restored while we wrote and let the process live on, so
            # this landed after it. `_restore` would do nothing: the handler
            # already cleared the flag.
            self._undo_reservation()
        elif self._resized and not after_resize:
            # A resize landed between marking and writing, so these are the old
            # margins and the handler already cleared the flag. Nobody else would
            # give them back.
            self._release_only()

    def _release_only(self) -> None:
        """Give the margins back and erase nothing."""
        self._write_atomic("\x1b7\x1b[r\x1b8")
        # Cleared after writing, the other way round from `_reserve`: a signal
        # in between finds a region it thinks is still set and releases it
        # again, and a doubled release costs nothing. Clearing first would
        # leave nobody to give it back.
        self._reserved = False

    def _undo_reservation(self) -> None:
        """Release a region that was set after somebody else restored."""
        self._write_atomic(self._restore_sequence())
        self._reserved = False

    def _restore_sequence(self) -> str:
        parts = ["\x1b[r"]  # release the region
        if self._resized or self._rewrapped:
            # A resize was seen and not worked out, so those rows may hold the
            # user's output. Give the margins back, erase nothing -- and keep
            # the cursor, which releasing the margins would otherwise home.
            return "\x1b7\x1b[r\x1b8"
        for position in range(self._reserved_rows):  # wipe what is left
            parts.append(f"\x1b[{self._top + position};1H\x1b[2K")
        parts.append(f"\x1b[{self._top};1H")
        return "".join(parts)

    def _close(self, raw: bool = False) -> None:
        if raw:
            # No lock on the way out of a signal: waiting on a mid-write thread
            # restores nothing at all, and a doubled restore is harmless.
            self._restore(raw=True)
        else:
            with self._lock:
                self._restore(raw=False)
        self._restore_handlers()
        self._restore_windows_console()
        with contextlib.suppress(Exception):
            atexit.unregister(self._close)

    def _restore(self, raw: bool) -> None:
        # Stop handing out rows and stop painting first: from here on the
        # dock is on its way out, and a bar bound to it would draw onto a
        # screen that no longer has a region for it.
        self._active = False
        # Only give back what was actually taken. Before the region is set
        # `_top` is still zero, and writing the sequence then would wipe the
        # top of the terminal rather than the reserved rows.
        if not self._reserved:
            return
        # A signal landing here re-enters and writes the same sequence: a
        # doubled restore is harmless, a missing one is not.
        if raw:
            self._write_raw(self._restore_sequence())
        else:
            self._write_atomic(self._restore_sequence())
        self._reserved = False

    def _enable_windows_escapes(self) -> bool:
        """Put the console into escape mode, ready to be put back."""
        if sys.platform != "win32":
            return True
        console = _windows_console_mode(self._fd)
        if console is None:
            return False
        handle, mode = console
        # Remember it first, for the same reason `_reserve` does: a signal
        # in between has to know there is a mode to put back.
        self._windows_console = console
        if not _set_windows_console_mode(
            handle, mode | _ENABLE_VIRTUAL_TERMINAL_PROCESSING
        ):
            # The probe worked and this did not. Rather than draw sequences
            # a console would show as text, do without the dock.
            self._windows_console = None
            return False
        return True

    def _restore_windows_console(self) -> None:
        if self._windows_console is None:
            return
        handle, mode = self._windows_console
        self._windows_console = None
        _set_windows_console_mode(handle, mode)

    # -- signals ----------------------------------------------------------

    def _install(self, number: int, handler: Any) -> None:
        # Read the old disposition first: `signal.signal` returns it only
        # once ours is installed, and a signal in between would find none
        # recorded and fall back to killing the process.
        with contextlib.suppress(ValueError, OSError):
            self._previous_handlers[number] = signal.getsignal(number)
            signal.signal(number, handler)

    def _install_handlers(self) -> None:
        for name in _CAUGHT_SIGNALS:
            self._install(getattr(signal, name), self._on_terminating_signal)
        if hasattr(signal, "SIGWINCH"):
            self._install(signal.SIGWINCH, self._on_resize)

    def _restore_handlers(self) -> None:
        ours = (self._on_terminating_signal, self._on_resize)
        for number, handler in self._previous_handlers.items():
            with contextlib.suppress(ValueError, OSError, TypeError):
                # Somebody may have installed their own handler while the
                # dock was open. Theirs is the newer one and stays.
                if signal.getsignal(number) in ours:
                    signal.signal(number, handler)
        self._previous_handlers.clear()

    def _on_terminating_signal(self, number: int, frame: FrameType | None) -> None:
        # Look the handler up first: closing puts the previous ones back and
        # forgets them.
        previous = self._previous_handlers.get(number, signal.SIG_DFL)
        # Put the terminal back before handing the signal on, so the shell
        # that gets control next is not stuck inside our region.
        self._close(raw=True)

        if previous is signal.SIG_IGN:
            # The process was started with this signal ignored, by `nohup`
            # or by a parent. Reserving rows must not turn that into a kill.
            return
        if callable(previous):
            previous(number, frame)
            return
        signal.signal(number, signal.SIG_DFL)
        os.kill(os.getpid(), number)

    def _on_resize(self, number: int, frame: FrameType | None) -> None:
        # At once, with the one sequence that needs no geometry: stale
        # margins make a resized window scroll into the reserved rows, and
        # a run waiting on the network may not repaint for minutes.
        if self._reserved and not self._painting:
            # Saved and restored around it: releasing the margins homes the
            # cursor. Not during a paint, though -- a terminal keeps one saved
            # cursor, and that paint needs it.
            self._write_raw("\x1b7\x1b[r\x1b8")
            self._reserved = False
        # Sample the width here rather than at the repaint: a window dragged
        # out and back before the next paint reflowed twice, and comparing
        # only the width we end up with would call that a height change.
        if self._measured_width() != self._reserved_width:
            self._rewrapped = True
        # Everything that needs measuring waits for the next `set()`:
        # redrawing from a handler can cut another write in half, and a torn
        # escape sequence is worse than a late repaint.
        self._resized = True
        previous = self._previous_handlers.get(number)
        if callable(previous):
            previous(number, frame)

    def _reopen_after_resize(self) -> None:
        """Rebuild the dock at the bottom of whatever the window is now.

        A reflow moves the old rows somewhere we cannot work out, so nothing
        is erased by their numbers and whatever they held stays where the
        terminal put it. The dock itself survives that: the new bottom is
        measurable and every line can say what it holds.
        """
        self._resized = False
        if self._reserved:
            # Usually the handler did this already. It skips a paint in
            # flight, which is the one case that gets here still reserved.
            self._release_only()

        height, width = self._measure()
        if not self._active:
            # A signal restored the terminal while we were measuring
            return
        shown = self._choose_layout(height)
        if shown is None:
            # No room at the moment. Not the end of the dock: callers get
            # plain bars until the window has the space again.
            self._paused = True
            return
        # What stood on the screen a moment ago, and how wide it was drawn
        # for. Read before the new geometry overwrites either.
        was_shown = [
            *([RULE * self.width] if self._rule else []),
            *(self._lines[row] for row in self._shown),
        ]
        was_width = self._reserved_width
        self._shown = shown

        self._width = width
        self._reserved_width = width
        self._reserved_height = height
        self._rewrapped = False
        self._paused = False
        self._top = height - self._reserved_rows + 1
        self._wipe_what_it_used_to_hold(was_shown, was_width)
        # No scrolling here, unlike the first open. What stands in those
        # rows a moment after a resize is the dock that was there before,
        # and scrolling it up is what put the old bars in the text flow and
        # sent them drifting. Every row below is erased as it is painted.
        self._reserve(height, after_resize=True)
        if not self._active:
            return
        if self._resized:
            # Another one arrived while we were setting these margins, so
            # they describe a window that is already gone. The next `set`
            # measures again; leaving them would pin the bars to nothing.
            self._release_only()
            return
        # Ask each line for its text again rather than repainting what was
        # measured for the old width: a bar rendered for 100 columns wraps
        # on 60 and takes the whole dock with it.
        self._paint_rule()
        for row in self._shown:
            render = self._renderers.get(row)
            if render is not None:
                self._lines[row] = render()
            self._paint(row, self._lines[row])
        if self._resized and self._reserved:
            # One arrived mid-repaint, during a row that kept the handler off
            # the margins. Nobody else would give these back.
            self._release_only()

    def _wipe_what_it_used_to_hold(self, was_shown: list[str], was_width: int) -> None:
        """Clear what the dock it used to be left standing above this one.

        Both docks sit on the bottom, so whatever the old one leaves over
        stands directly above where the new one starts, and no later paint
        reaches it.

        There is more of it than there were rows. A narrower window rewraps
        each line, so a row drawn for a hundred columns comes back as three
        at forty-five. How many follows from the rows themselves rather
        than from their count: a slot waiting with just its number does not
        wrap at all. Widening joins nothing back -- these are hard lines --
        so it only leaves them short.
        """
        if self._width >= was_width:
            standing = len(was_shown)
        else:
            standing = sum(max(1, -(-len(text) // self._width)) for text in was_shown)
        extra = min(standing - self._reserved_rows, self._top - 1)
        if extra <= 0:
            return
        self._paint_write(
            "\x1b7"
            + "".join(
                f"\x1b[{line};1H\x1b[2K" for line in range(self._top - extra, self._top)
            )
            + "\x1b8"
        )

    def _scroll_room_into_being(self, height: int) -> None:
        """Make the reserved rows by scrolling, not by writing over them.

        A line feed only scrolls from the last row; anywhere above it just
        moves down. Coming out of a reflow the cursor can be anywhere, and
        the rows would then be cleared with the user's output still in them.
        """
        self._write_atomic(f"\x1b[{height};1H" + "\n" * self._reserved_rows)

    # -- painting ---------------------------------------------------------

    def set_renderer(self, row: int, render: Callable[[], str] | None) -> None:
        """Let a row redraw itself, for when the rows move under it."""
        with self._lock:
            if render is None:
                self._renderers.pop(row, None)
            else:
                self._renderers[row] = render

    def set(self, row: int, text: str) -> None:
        with self._lock:
            self._lines[row] = text
            if not self._active:
                return
            if self._resized or self._moved():
                self._reopen_after_resize()
                # It painted every row already, and asked each for text at
                # the new width. Painting `text` on top would put back what
                # was measured for the old one.
                self._flush()
                if not self._active:
                    return
                self._undo_if_gone()
                return
            if self._paused:
                # No room to draw in. The text is kept, so the row comes
                # back with what it holds once there is.
                return
            self._paint(row, text)
            self._flush()
            self._undo_if_gone()

    def _moved(self) -> bool:
        """Whether the window is no longer the one we reserved in.

        The handler runs between two bytecodes, so a resize can be minutes
        old in machine terms before it is noticed -- and a row painted in
        that gap goes to a line number the terminal has already moved. One
        measurement is cheap enough to make that impossible.
        """
        try:
            return self._measure() != (self._reserved_height, self._reserved_width)
        except OSError:
            return False

    def _undo_if_gone(self) -> None:
        if not self._active:
            # The paint was in flight while a signal restored. Whatever of
            # it reached the screen has to go again.
            self._undo_reservation()

    def _paint(self, row: int, text: str) -> None:
        if self._resized:
            # A signal runs between two bytecodes, so the flag can be set
            # after `set` looked at it and after any row of a repaint. The
            # rows this addresses are not ours until it has been worked out.
            return
        if not self._active:
            # A signal may have restored the terminal between two rows of a
            # repaint. Writing on would put bars back onto the normal screen.
            return
        if self._moved():
            # The window changed and the handler has not run yet. These row
            # numbers belong to a screen that is already gone.
            self._resized = True
            return
        if row not in self._shown:
            # A row the window has no space for. It keeps its text and comes
            # back with it when the space does.
            return
        position = self._shown.index(row)
        # One write, not three: a handler runs between bytecodes and would
        # cut a split sequence in half. Not a guarantee, but the text has no
        # newline, so a line-buffered stream holds it.
        self._painting = True
        self._paint_write(
            "\x1b7"  # save cursor
            f"\x1b[{self._line_of(position)};1H\x1b[2K{text}"  # absolute row
            "\x1b8"  # put it back
        )
        self._painting = False

    def _paint_rule(self) -> None:
        """Draw the line that sets the dock off from the output above it."""
        if not self._rule or self._resized or not self._active:
            return
        self._paint_write(
            "\x1b7"  # save cursor
            f"\x1b[{self._top};1H\x1b[2K{RULE * self.width}"
            "\x1b8"  # put it back
        )

    def _paint_write(self, text: str) -> None:
        """Write and flush a paint before the handler may come between.

        The paint carries no newline, so a line-buffered stream keeps it. A
        resize landing after the write but before the flush would put its
        release out first and this on top of it, on rows that are nobody's.
        """
        self._write_atomic(text)

    @property
    def width(self) -> int:
        """How wide a row may draw, which is one short of the window.

        Filling the last column puts a terminal into pending wrap and it
        marks the line as continued. On a widen it then pulls the next row
        back onto it, and the rule and every bar arrive as one glued line.
        Leaving a column free costs nothing and keeps them separate.
        """
        return max(1, self._width - 1)

    def _write_atomic(self, text: str) -> None:
        """One whole sequence onto the terminal, with nobody in between.

        The log handler takes the same lock, so a line cannot land inside
        the escape sequence a row is being drawn with. Never from a signal
        handler: one that waits on a lock restores nothing at all.
        """
        with atomic():
            self._write(text)
            self._flush()

    def _write(self, text: str) -> None:
        # RuntimeError is what a buffered writer raises when a signal handler
        # re-enters it. Losing that paint is fine; raising into the download
        # loop is not.
        with contextlib.suppress(ValueError, OSError, RuntimeError):
            self._stream.write(text)

    def _flush(self) -> None:
        with contextlib.suppress(ValueError, OSError, RuntimeError):
            self._stream.flush()

    def _write_raw(self, text: str) -> None:
        """Write past the buffering, for use from a signal handler."""
        if self._fd is None:
            # A stream that is a terminal but has no descriptor. The
            # buffered write may refuse to be re-entered, and then the
            # region stays; trying is still better than never restoring.
            self._write(text)
            self._flush()
            return

        data = text.encode("ascii", "replace")
        with contextlib.suppress(ValueError, OSError):
            while data:
                written = os.write(self._fd, data)
                if written <= 0:
                    return
                data = data[written:]


def _elide(text: str, width: int) -> str:
    """Shorten to `width`, dropping the middle.

    The ends carry what tells two downloads apart: the series at the front,
    the episode and the extension at the back.
    """
    if width <= 0:
        return ""
    if len(text) <= width:
        return text
    if width == 1:
        return "\u2026"
    head = (width - 1) // 2
    return text[:head] + "\u2026" + text[len(text) - (width - 1 - head) :]


def _prefix_budget(ncols: int, bar_format: str | None, **meter: Any) -> int:
    """Columns the description may take before the meter starts to suffer.

    Every column the name takes comes off the bar, and once the bar is gone
    tqdm truncates the line itself -- percentage, counts and all, which is
    how a long title left nothing but a cut-off title. Rather than guess at
    the width of a formatted size, ask tqdm what the bar would be with no
    name at all: that is exactly what there is to spend.
    """
    probe = tqdm.tqdm.format_meter(
        prefix="", ncols=ncols, bar_format=bar_format, **meter
    )
    opening = probe.find("|")
    closing = probe.find("|", opening + 1)
    if opening < 0 or closing < 0:
        # No bar to take from, so nothing is safe to spend
        return 0
    return max(0, closing - opening - 1 - MIN_BAR_COLUMNS)


class DockedProgressBar:
    """One reserved row, rendered by tqdm and placed by the dock."""

    def __init__(
        self,
        dock: Dock,
        row: int,
        *,
        description: str,
        total: int,
        start: int = 0,
        label: str = "",
    ) -> None:
        self._dock = dock
        self._row = row
        self._label = label
        # Not stripped: the label is padded so the numbers line up past
        # nine, and stripping would undo that for the single digits.
        self._lead = f"{label} " if label else ""
        self._name = description
        self._total = total
        self._n = start
        self._started = time.monotonic()
        self._last_paint = 0.0
        self._closed = False
        dock.set_renderer(row, self._text)
        self._render(force=True)

    def __enter__(self) -> DockedProgressBar:
        return self

    def __exit__(self, *exc: object) -> bool:
        self.close()
        return False

    def update(self, n: int = 1) -> None:
        self._n += n
        self._render()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        # Leave the number behind rather than a blank line, so a slot that
        # is waiting for work still reads as that slot.
        self._dock.set_renderer(self._row, None)
        self._dock.set(self._row, self._label)
        _release_row(self._dock, self._row)

    def _render(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_paint < MIN_REPAINT_INTERVAL:
            return
        self._last_paint = now
        self._dock.set(self._row, self._text())
        # The row below keeps the same cadence, so its clock runs and a
        # paint that did not land is made good.
        _tick_summary()

    def _text(self) -> str:
        return self._format(time.monotonic() - self._started)

    def _format(self, elapsed: float) -> str:
        meter: dict[str, Any] = {
            "n": self._n,
            "total": self._total,
            "elapsed": elapsed,
            "unit": "B",
            "unit_scale": True,
            "unit_divisor": 1024,
        }
        ncols = self._dock.width
        # The number is never shortened. It names the slot, and a slot that
        # cannot be told from its neighbour is worth less than the two
        # columns it gives back.
        bar_format = None
        room = _prefix_budget(ncols, None, **meter) - len(self._lead)
        if room < MIN_NAME_COLUMNS:
            bar_format = COMPACT_METER
            room = _prefix_budget(ncols, bar_format, **meter) - len(self._lead)
        return tqdm.tqdm.format_meter(
            ncols=ncols,
            bar_format=bar_format,
            prefix=self._lead + _elide(self._name, room),
            **meter,
        )


# -- the process-wide dock -------------------------------------------------


class _Summary:
    """The last reserved row, counting jobs off against a total.

    Both numbers are the caller's, and the total can grow.
    """

    def __init__(self, dock: Dock, row: int, total: int) -> None:
        self._dock = dock
        self._row = row
        self._total = total
        self._done = 0
        self._started = time.monotonic()
        self._last_paint = 0.0
        dock.set_renderer(row, self._text)
        self.render()

    def advance(self, n: int = 1) -> None:
        self._done += n
        self.render()

    def grow(self, n: int = 1) -> None:
        """Take note of work that did not exist when the total was taken."""
        self._total += n
        self.render()

    def render(self, force: bool = True) -> None:
        """Draw it again.

        Unforced this is the heartbeat the counts alone do not give it: a
        long run finishes nothing for minutes, and until then the clock
        stands still and a paint cut short by a resize stays cut.
        """
        now = time.monotonic()
        if not force and now - self._last_paint < MIN_REPAINT_INTERVAL:
            return
        self._last_paint = now
        self._dock.set(self._row, self._text())

    def _text(self) -> str:
        meter: dict[str, Any] = {
            "n": self._done,
            "total": self._total,
            "elapsed": time.monotonic() - self._started,
            "unit": "job",
        }
        ncols = self._dock.width
        # Nothing here can be shortened, so the clock goes as soon as the
        # word no longer fits beside a bar worth drawing.
        fits = _prefix_budget(ncols, None, **meter) >= len(SUMMARY_PREFIX)
        return tqdm.tqdm.format_meter(
            ncols=ncols,
            bar_format=None if fits else COMPACT_METER,
            prefix=SUMMARY_PREFIX,
            **meter,
        )


class _Current:
    """The dock this process is using, if any."""

    dock: Dock | None = None
    free_rows: list[int] = []  # noqa: RUF012
    labels: list[str] = []  # noqa: RUF012
    summary: _Summary | None = None
    disabled: bool = False


_current = _Current()
_rows_lock = threading.Lock()


def _release_row(dock: Dock, row: int) -> None:
    with _rows_lock:
        # A bar outliving its dock must not hand its row to the dock that
        # came after: that row already belongs to somebody else.
        if _current.dock is not dock:
            return
        if row not in _current.free_rows:
            _current.free_rows.append(row)


def _tick_summary() -> None:
    with _rows_lock:
        summary = _current.summary
    if summary is not None:
        summary.render(force=False)


def advance_summary(n: int = 1) -> None:
    """Count `n` jobs off against the total, if a summary is being shown."""
    with _rows_lock:
        summary = _current.summary
    if summary is not None:
        summary.advance(n)


def grow_summary_total(n: int = 1) -> None:
    """Add `n` to the total, for work discovered after the run started.

    A title that comes in parts queues one job per part. A call before the
    block opens is ignored: those jobs are already in the count it took.
    """
    with _rows_lock:
        summary = _current.summary
    if summary is not None:
        summary.grow(n)


def progress_disabled() -> bool:
    """Whether the caller asked for no progress bars at all."""
    return _current.disabled


@contextlib.contextmanager
def docked_progress(
    rows: int,
    enabled: bool = True,
    stream: IO[str] | None = None,
    total: int | None = None,
) -> Iterator[None]:
    """Reserve `rows` lines for progress bars for the length of this block.

    Each line keeps its number and shows it even while idle, so a waiting
    slot reads as one rather than as a line fewer. With `total` given, one
    more line below counts how much of it is behind us. With `enabled`
    false nothing is drawn at all. Nesting is a no-op.
    """
    with _rows_lock:
        busy = _current.dock is not None or _current.disabled
    if busy:
        yield
        return

    if not enabled:
        with _rows_lock:
            _current.disabled = True
        try:
            yield
        finally:
            with _rows_lock:
                _current.disabled = False
        return

    width = len(str(rows))
    labels = [f"{i + 1:>{width}}." for i in range(rows)]
    # The running total is the row kept when the window has no space for
    # the rest: one line still says how far along the whole run is.
    dock = Dock(
        rows + (1 if total is not None else 0),
        stream=stream,
        keep_row=rows if total is not None else None,
    )
    if not dock.enabled:
        yield
        return

    with _rows_lock:
        # Check and claim under one lock, or two blocks both reserve a
        # region. No rows yet: the dock is not open.
        taken = _current.dock is not None or _current.disabled
        if not taken:
            _current.dock = dock
            _current.free_rows = []
    if taken:
        yield
        return

    try:
        with dock:
            with _rows_lock:
                if _current.dock is dock:
                    _current.free_rows = list(range(rows))
                    _current.labels = labels
                    if total is not None:
                        _current.summary = _Summary(dock, rows, total)
            for row, label in enumerate(labels):
                dock.set(row, label)
            yield
    finally:
        # After the dock has given its region back, not before: clearing
        # first would let a block starting in another thread reserve a new
        # region that this one's restore then wipes out.
        with _rows_lock:
            # Only give the slot up if it is still ours. A block that took
            # over is the one entitled to clear it.
            if _current.dock is dock:
                _current.dock = None
                _current.free_rows = []
                _current.labels = []
                _current.summary = None


def take_progressbar(
    destination: pathlib.Path, total: int, start: int = 0
) -> DockedProgressBar | None:
    """A docked bar, or None when there is no dock or no row left."""
    with _rows_lock:
        if _current.disabled:
            return None
        dock = _current.dock

    if dock is None:
        return None
    # Before claiming a row: a resize noted but not yet worked out may have
    # taken the dock down, and the bar would draw into nothing.
    dock.settle()

    with _rows_lock:
        # A row is handed out even when the window is currently too small to
        # show it. It keeps counting, and it appears the moment there is
        # room -- which a bar drawn some other way never would.
        if _current.dock is not dock or not dock.active or not _current.free_rows:
            return None
        row = _current.free_rows.pop(0)
        label = _current.labels[row] if row < len(_current.labels) else ""

    return DockedProgressBar(
        dock,
        row,
        description=click.format_filename(destination, shorten=True),
        total=total,
        start=start,
        label=label,
    )
