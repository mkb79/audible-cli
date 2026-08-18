"""The one lock for writing to the terminal.

While a download runs, two things write to the same screen: the progress
dock, which holds the bottom rows with a scroll region, and the log
handler. A log line arriving in the middle of the dock's escape sequence
cuts it in half, and the bar ends up drawn wherever the cursor happened to
be. Nothing at either call site can prevent that on its own.

There is no need to invent a lock for it. tqdm keeps a process-wide write
lock and `tqdm.external_write_mode` takes it, which is what the log handler
already writes through. So the rule is that everyone putting a whole thing
on the terminal takes that same lock.

What this deliberately does not do is stand in front of `sys.stdout`.
Nothing else writes while the dock is up: over the whole download path
there is no `print`, no `click.echo` and no direct stream write, only the
logger. A proxy would be paid for by every other command, and by anything
that asks a stream what it is, to catch writers that are not there.
"""

from __future__ import annotations

import contextlib
import threading
from collections.abc import Iterator

import tqdm


def write_lock() -> threading.Lock:
    """The lock every terminal writer takes.

    tqdm's, because the log handler is already inside it by way of
    `tqdm.external_write_mode`.
    """
    return tqdm.tqdm.get_lock()


@contextlib.contextmanager
def atomic() -> Iterator[None]:
    """Hold the terminal for one whole write.

    Not re-entrant: the lock underneath is a plain one, so a block holding
    it must not contain another. Keep formatting outside it, because a
    terminal that has stopped reading holds up everyone waiting here.
    """
    lock = write_lock()
    lock.acquire()
    try:
        yield
    finally:
        lock.release()


__all__ = ["atomic", "write_lock"]
