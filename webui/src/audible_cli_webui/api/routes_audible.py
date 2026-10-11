from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import models, schemas
from ..audible_bridge import auth_store, login_flow
from ..auth.session import SessionData
from ..deps import get_current_session, get_current_user, get_db

router = APIRouter(prefix="/api/audible")


@router.get("/account", response_model=schemas.AudibleAccountInfo)
async def get_account(
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> schemas.AudibleAccountInfo:
    account = auth_store.get_account(db, user.id)
    if account is None:
        return schemas.AudibleAccountInfo(connected=False)
    return schemas.AudibleAccountInfo(
        connected=True,
        marketplace=account.marketplace,
        with_username=account.with_username,
        status=account.status.value,
        connected_at=account.connected_at,
    )


@router.post("/connect/start", response_model=schemas.LoginUrlResponse)
async def connect_start(
    body: schemas.StartConnectRequest,
    session: SessionData = Depends(get_current_session),
) -> schemas.LoginUrlResponse:
    login_url, pending_state = login_flow.start_login(body.marketplace, body.with_username)
    session.pending_audible_login = pending_state
    return schemas.LoginUrlResponse(login_url=login_url)


@router.post("/connect/complete")
async def connect_complete(
    body: schemas.CompleteConnectRequest,
    user: models.User = Depends(get_current_user),
    session: SessionData = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> dict:
    pending = session.pending_audible_login
    if pending is None:
        raise HTTPException(status_code=400, detail="No login in progress. Generate a login link first.")
    session.pending_audible_login = None

    try:
        auth_dict = login_flow.complete_login(pending, body.pasted_url)
    except login_flow.AudibleConnectError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    auth_store.save_account(db, user.id, pending["marketplace"], pending["with_username"], auth_dict)
    return {"ok": True}


@router.delete("/account", status_code=204)
async def disconnect_account(
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    auth_store.delete_account(db, user.id)
