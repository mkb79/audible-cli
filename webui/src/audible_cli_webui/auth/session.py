import secrets
import time
from dataclasses import dataclass, field
from typing import Any

from fastapi import Request, Response

from ..settings import settings

SESSION_COOKIE_NAME = "webui_session"
# Bounded absolute lifetime: bounds how stale a cached is_admin claim can
# get, and forces periodic re-login (re-validation against Authelia) so a
# disabled LDAP account eventually stops working here too.
SESSION_TTL_SECONDS = 12 * 60 * 60


@dataclass
class SessionData:
    user_id: int | None = None
    is_admin: bool = False
    display_name: str | None = None
    created_at: float = field(default_factory=time.time)
    # Ephemeral Audible external-login PKCE state between "generate login
    # URL" and "submit pasted-back URL" — single-use, per-browser-session.
    pending_audible_login: dict[str, Any] | None = None


# In-process only: adequate for the single-container deployment this app
# targets (see webui README). A restart invalidates sessions, forcing
# re-login — an acceptable v1 tradeoff, not a correctness issue.
_store: dict[str, SessionData] = {}


def _purge_expired() -> None:
    now = time.time()
    expired = [sid for sid, data in _store.items() if now - data.created_at > SESSION_TTL_SECONDS]
    for sid in expired:
        _store.pop(sid, None)


def create_session() -> tuple[str, SessionData]:
    _purge_expired()
    session_id = secrets.token_urlsafe(32)
    data = SessionData()
    _store[session_id] = data
    return session_id, data


def get_session(request: Request) -> SessionData | None:
    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    if not session_id:
        return None
    _purge_expired()
    return _store.get(session_id)


def set_session_cookie(response: Response, session_id: str) -> None:
    response.set_cookie(
        SESSION_COOKIE_NAME,
        session_id,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        max_age=SESSION_TTL_SECONDS,
        path="/",
    )


def clear_session(request: Request, response: Response) -> None:
    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    if session_id:
        _store.pop(session_id, None)
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
