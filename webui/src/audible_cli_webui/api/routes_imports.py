from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models, schemas
from ..deps import get_current_user, get_db
from ..importer.jobs import job_manager
from ..importer.targets import missing_setup_reason

router = APIRouter(prefix="/api/imports")


@router.post("")
async def trigger_import(
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    if job_manager.is_user_busy(user.id):
        raise HTTPException(status_code=409, detail="An import is already running for your account.")
    # Refuse up front, so a setup gap doesn't show up later as a failed run.
    reason = missing_setup_reason(db, user.id)
    if reason is not None:
        raise HTTPException(status_code=400, detail=reason)

    run = models.ImportRun(user_id=user.id)
    db.add(run)
    db.commit()
    db.refresh(run)

    job_manager.start_run(run.id, user.id)
    return {"run_id": run.id}


@router.get("/readiness", response_model=schemas.ImportReadinessOut)
async def import_readiness(
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> schemas.ImportReadinessOut:
    reason = missing_setup_reason(db, user.id)
    return schemas.ImportReadinessOut(ready=reason is None, reason=reason)


@router.get("", response_model=list[schemas.ImportRunOut])
async def list_imports(
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[schemas.ImportRunOut]:
    runs = (
        db.execute(
            select(models.ImportRun).where(models.ImportRun.user_id == user.id).order_by(models.ImportRun.id.desc())
        )
        .scalars()
        .all()
    )
    return [schemas.ImportRunOut.from_orm_run(r) for r in runs]


@router.get("/current", response_model=schemas.ImportRunDetailOut | None)
async def current_import(
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> schemas.ImportRunDetailOut | None:
    run = (
        db.execute(
            select(models.ImportRun)
            .where(
                models.ImportRun.user_id == user.id,
                models.ImportRun.status.in_([models.ImportRunStatus.QUEUED, models.ImportRunStatus.RUNNING]),
            )
            .order_by(models.ImportRun.id.desc())
        )
        .scalars()
        .first()
    )
    if run is None:
        return None
    return schemas.ImportRunDetailOut.from_orm_run_with_items(run)


@router.get("/{run_id}", response_model=schemas.ImportRunDetailOut)
async def get_import(
    run_id: int,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> schemas.ImportRunDetailOut:
    run = db.get(models.ImportRun, run_id)
    if run is None or (run.user_id != user.id and not user.is_admin):
        raise HTTPException(status_code=404, detail="Import run not found.")
    return schemas.ImportRunDetailOut.from_orm_run_with_items(run)
