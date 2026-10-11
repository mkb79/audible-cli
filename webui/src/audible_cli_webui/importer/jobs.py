import asyncio
import logging
from datetime import UTC, datetime

from sqlalchemy import select

from .. import models
from ..db import SessionLocal
from ..settings import settings
from .orchestrator import run_import

logger = logging.getLogger("audible_cli_webui.importer.jobs")


class JobManager:
    """Per-tenant run lock + bounded overall concurrency.

    Deliberately holds no module-level mutable queue/state of its own (the
    one thing this exists to avoid is the cmd_download.py QUEUE hazard —
    see orchestrator.py) — each run gets its own locally-scoped asyncio
    primitives inside run_import().
    """

    def __init__(self, max_concurrent_runs: int) -> None:
        self._semaphore = asyncio.Semaphore(max_concurrent_runs)
        self._active_user_ids: set[int] = set()

    def is_user_busy(self, user_id: int) -> bool:
        return user_id in self._active_user_ids

    def start_run(self, run_id: int, user_id: int) -> None:
        self._active_user_ids.add(user_id)
        task = asyncio.create_task(self._run(run_id, user_id))
        task.add_done_callback(lambda _task: self._active_user_ids.discard(user_id))

    async def _run(self, run_id: int, user_id: int) -> None:
        async with self._semaphore:
            try:
                await run_import(run_id, user_id)
            except Exception:
                # A bug in the orchestrator must never leave a run stuck
                # "running" forever — record it as failed instead.
                logger.exception("Import run %s for user %s failed unexpectedly", run_id, user_id)
                with SessionLocal() as db:
                    run = db.get(models.ImportRun, run_id)
                    if run is not None and run.status != models.ImportRunStatus.SUCCEEDED:
                        run.status = models.ImportRunStatus.FAILED
                        run.error_message = run.error_message or "Internal error during import."
                        run.finished_at = datetime.now(UTC)
                        db.commit()


def recover_interrupted_runs() -> None:
    """Mark any run left 'running' from a prior process lifetime as failed.

    An asyncio.Task cannot survive a process restart, so any row still
    'running' at startup is stale by construction.
    """
    with SessionLocal() as db:
        stale = (
            db.execute(select(models.ImportRun).where(models.ImportRun.status == models.ImportRunStatus.RUNNING))
            .scalars()
            .all()
        )
        for run in stale:
            run.status = models.ImportRunStatus.FAILED
            run.error_message = "Interrupted by a server restart."
            run.finished_at = datetime.now(UTC)
        if stale:
            db.commit()
            logger.warning("Marked %d interrupted import run(s) as failed at startup.", len(stale))


job_manager = JobManager(settings.max_concurrent_runs)
