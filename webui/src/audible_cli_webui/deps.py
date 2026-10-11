from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from . import models
from .auth.session import SessionData, get_session
from .db import get_db

__all__ = ["get_current_session", "get_current_user", "get_db", "require_admin"]


def get_current_session(request: Request) -> SessionData:
    session = get_session(request)
    if session is None or session.user_id is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return session


def get_current_user(
    session: SessionData = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> models.User:
    user = db.get(models.User, session.user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


def require_admin(user: models.User = Depends(get_current_user)) -> models.User:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin privileges required")
    return user
