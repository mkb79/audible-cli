"""The download queue's behaviour on failing jobs.

Covers the deadlock from #235/#239, where a failing job killed its consumer
and QUEUE.join() then waited forever, and the exit code from #256.
"""

import ast
import asyncio
import inspect
import logging

import httpx
import pytest

from audible_cli import progress
from audible_cli.cmds import cmd_download
from audible_cli.cmds.cmd_download import DownloadRun, consume, drain_queue
from audible_cli.exceptions import AudibleCliException


@pytest.fixture(autouse=True)
def isolate_queue(monkeypatch):
    """Restore the module-level QUEUE after every test.

    monkeypatch records the original here, so the direct assignments the
    tests make below are undone at teardown regardless.
    """
    monkeypatch.setattr(cmd_download, "QUEUE", None)


async def good(n):
    await asyncio.sleep(0)


async def bad(n):
    raise RuntimeError(f"job {n} failed")


async def slow(n, finished):
    await asyncio.sleep(0.1)
    finished.append(n)


def run_queue(jobs, sim_jobs, ignore_errors, timeout=5.0):
    """Work a list of jobs off the real queue with the real consumers."""

    async def main():
        cmd_download.QUEUE = asyncio.Queue()
        run = DownloadRun(ignore_errors)
        for i, job in enumerate(jobs):
            cmd_download.QUEUE.put_nowait((job, {"n": i}))

        consumers = [asyncio.create_task(consume(run)) for _ in range(sim_jobs)]
        try:
            await asyncio.wait_for(cmd_download.QUEUE.join(), timeout=timeout)
        finally:
            for consumer in consumers:
                consumer.cancel()
            await asyncio.gather(*consumers, return_exceptions=True)

        return run

    return asyncio.run(main())


@pytest.mark.parametrize(
    "jobs, sim_jobs",
    [
        # Enough failures to kill every consumer, which used to strand the
        # queue and hang QUEUE.join() forever
        ([bad] * 3 + [good] * 5, 3),
        ([bad, good, good], 1),
        ([bad] * 10, 3),
    ],
)
def test_failures_never_strand_the_queue(jobs, sim_jobs):
    run = run_queue(jobs, sim_jobs, ignore_errors=False)

    assert cmd_download.QUEUE.qsize() == 0
    assert run.errors


def test_first_failure_stops_the_queued_jobs():
    run = run_queue([bad] + [good] * 20, sim_jobs=1, ignore_errors=False)

    assert len(run.errors) == 1
    assert run.skipped == 20


def test_ignore_errors_runs_everything():
    run = run_queue([bad] * 3 + [good] * 5, sim_jobs=3, ignore_errors=True)

    assert len(run.errors) == 3
    assert run.skipped == 0


def test_a_clean_run_reports_nothing():
    run = run_queue([good] * 10, sim_jobs=3, ignore_errors=False)

    assert (run.errors, run.skipped) == ([], 0)
    run.raise_for_errors()  # must not raise


def test_running_downloads_are_not_cut_short():
    finished = []

    async def main():
        cmd_download.QUEUE = asyncio.Queue()
        run = DownloadRun(ignore_errors=False)
        # Two slow jobs occupy consumers while the third one fails
        cmd_download.QUEUE.put_nowait((slow, {"n": 0, "finished": finished}))
        cmd_download.QUEUE.put_nowait((slow, {"n": 1, "finished": finished}))
        cmd_download.QUEUE.put_nowait((bad, {"n": 2}))
        for i in range(3, 8):
            cmd_download.QUEUE.put_nowait((good, {"n": i}))

        consumers = [asyncio.create_task(consume(run)) for _ in range(3)]
        await asyncio.wait_for(cmd_download.QUEUE.join(), timeout=5.0)
        for consumer in consumers:
            consumer.cancel()
        await asyncio.gather(*consumers, return_exceptions=True)
        return run

    run = asyncio.run(main())

    assert sorted(finished) == [0, 1]
    assert run.skipped == 5


def test_raise_for_errors_reports_failed_and_skipped():
    run = run_queue([bad] + [good] * 4, sim_jobs=1, ignore_errors=False)

    with pytest.raises(AudibleCliException) as excinfo:
        run.raise_for_errors()

    message = str(excinfo.value)
    assert "1 job(s) failed" in message
    assert "4 skipped" in message
    assert "--ignore-errors" in message


def test_raise_for_errors_also_fires_with_ignore_errors():
    # #256: a run that saw failures must not report success
    run = run_queue([bad] * 2 + [good] * 3, sim_jobs=3, ignore_errors=True)

    with pytest.raises(AudibleCliException, match="2 job\\(s\\) failed"):
        run.raise_for_errors()


def test_drain_queue_reports_a_consumer_that_ends_early():
    async def dying_consumer(run):
        await asyncio.sleep(0)
        raise asyncio.CancelledError()

    async def main():
        cmd_download.QUEUE = asyncio.Queue()
        for i in range(20):
            cmd_download.QUEUE.put_nowait((good, {"n": i}))

        original = cmd_download.consume
        cmd_download.consume = dying_consumer
        try:
            await asyncio.wait_for(
                drain_queue(DownloadRun(ignore_errors=False), 1), timeout=5.0
            )
        finally:
            cmd_download.consume = original

    # Waiting on QUEUE.join() alone would hang here instead
    with pytest.raises(AudibleCliException, match="stopped unexpectedly"):
        asyncio.run(main())


def test_drain_queue_completes_a_normal_run():
    async def main():
        cmd_download.QUEUE = asyncio.Queue()
        for i in range(20):
            cmd_download.QUEUE.put_nowait((good, {"n": i}))
        run = DownloadRun(ignore_errors=False)
        await asyncio.wait_for(drain_queue(run, 3), timeout=5.0)
        return run

    run = asyncio.run(main())

    assert (run.errors, run.skipped) == ([], 0)


class NamedItem:
    asin = "B0FQJHTG19"
    full_title = "Star Wars: Die Hand von Thrawn"


async def disconnects(item):
    raise httpx.RemoteProtocolError("Server disconnected without sending a response.")


def test_a_failure_says_which_job_it_was(caplog):
    # "Server disconnected without sending a response" on its own names
    # neither the title nor the kind of file, and a run of hundreds of jobs
    # gives no way to find out which one to retry.
    async def main():
        cmd_download.QUEUE = asyncio.Queue()
        run = DownloadRun(ignore_errors=True)
        cmd_download.QUEUE.put_nowait((disconnects, {"item": NamedItem()}))
        consumer = asyncio.create_task(consume(run))
        await asyncio.wait_for(cmd_download.QUEUE.join(), timeout=5)
        consumer.cancel()
        await asyncio.gather(consumer, return_exceptions=True)

    with caplog.at_level(logging.ERROR, logger="audible_cli.cmds.cmd_download"):
        asyncio.run(main())

    line = caplog.text
    assert "disconnects" in line, "which kind of job"
    assert "B0FQJHTG19" in line, "which title, by asin"
    assert "Die Hand von Thrawn" in line, "and by name"
    assert "RemoteProtocolError" in line, "and what actually went wrong"
    assert "Server disconnected" in line


# --- what the running total sees while the queue drains -------------------


class RecordingSummary:
    """Stands in for the dock's summary line and just counts."""

    def __init__(self):
        self.done = 0
        self.total = 0

    def advance(self, n=1):
        self.done += n

    def grow(self, n=1):
        self.total += n


@pytest.fixture
def summary(monkeypatch):
    """Publish a summary the real consumers will find."""
    recorder = RecordingSummary()
    monkeypatch.setattr(progress._current, "summary", recorder, raising=False)
    return recorder


@pytest.mark.parametrize(
    ("jobs", "ignore_errors"),
    [
        ([good, good, good], False),
        ([bad, good, good], True),  # a failure is still one job handled
        ([bad, good, good], False),  # and so is one skipped after the abort
    ],
)
def test_every_queue_entry_is_counted_once(summary, jobs, ignore_errors):
    # The count answers "how much of the queue is behind us", so it has to
    # move for jobs that ran, failed, and were skipped alike. Without the
    # call in consume()'s finally this stays at zero.
    run_queue(jobs, sim_jobs=2, ignore_errors=ignore_errors)

    assert summary.done == len(jobs)


def test_a_job_queued_while_draining_grows_the_total(summary):
    # A book that comes in parts adds its jobs after the total was taken.
    # Counting them without growing the total runs the display past 100%.
    async def spawns_two(n):
        for child in range(2):
            cmd_download.enqueue(good, {"n": f"{n}.{child}"})

    run_queue([spawns_two], sim_jobs=2, ignore_errors=False)

    assert summary.done == 3, "the parent and both of its parts"
    assert summary.total == 2, "the two parts were not in the original count"


class FakeItem:
    def create_base_filename(self, mode, length):
        return "a-title"


def queue_one_pdf():
    """Put exactly one job on the queue through the real queue_job()."""
    cmd_download.queue_job(
        get_cover=False,
        get_pdf=True,
        get_annotation=False,
        get_chapters=False,
        get_aax=False,
        get_aaxc=False,
        client=None,
        output_dir="/nowhere",
        filename_mode="config",
        filename_length=230,
        item=FakeItem(),
        cover_sizes=[],
        chapter_type="Tree",
        quality="best",
        overwrite_existing=False,
        aax_fallback=False,
    )


def test_queueing_a_job_the_real_way_grows_the_total(summary, monkeypatch):
    # Not `enqueue()` directly: the point is that the production path goes
    # through it. Calling put_nowait anywhere else would leave the running
    # total short by exactly the jobs that took the shortcut.
    monkeypatch.setattr(cmd_download, "QUEUE", asyncio.Queue())

    queue_one_pdf()

    assert cmd_download.QUEUE.qsize() == 1
    assert summary.total == 1, "the queued job was not counted"


def test_nothing_reaches_the_queue_behind_the_counter():
    """Every put has to go through `enqueue`, which is what counts it.

    Read from the syntax tree because the miscount is invisible at runtime
    until a title comes in parts: a bare `put_nowait` elsewhere still
    queues the work, it just never shows up in the denominator.
    """
    tree = ast.parse(inspect.getsource(cmd_download))
    outside = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for call in ast.walk(node):
            if (
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and call.func.attr == "put_nowait"
                and node.name != "enqueue"
            ):
                outside.append(f"{node.name}:{call.lineno}")

    assert outside == [], f"queue puts that bypass enqueue(): {outside}"
