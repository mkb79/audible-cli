"""The download queue's behaviour on failing jobs.

Covers the deadlock from #235/#239, where a failing job killed its consumer
and QUEUE.join() then waited forever, and the exit code from #256.
"""

import asyncio
import logging

import httpx
import pytest

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
