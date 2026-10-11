from datetime import UTC, datetime

from authlib.integrations.starlette_client import OAuth
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from .. import models
from ..db import SessionLocal
from ..settings import settings
from .session import clear_session, create_session, set_session_cookie

router = APIRouter()

oauth = OAuth()
oauth.register(
    name="authelia",
    server_metadata_url=f"{settings.oidc_issuer}/.well-known/openid-configuration",
    client_id=settings.oidc_client_id,
    client_secret=settings.oidc_client_secret,
    client_kwargs={"scope": "openid profile email groups"},
)


@router.get("/auth/login")
async def login(request: Request):
    redirect_uri = f"{settings.public_base_url}/auth/callback"
    return await oauth.authelia.authorize_redirect(request, redirect_uri)


@router.get("/auth/callback")
async def callback(request: Request):
    token = await oauth.authelia.authorize_access_token(request)
    claims = await oauth.authelia.userinfo(token=token)

    subject: str = claims["sub"]
    groups: list[str] = claims.get("groups") or []
    is_admin = settings.oidc_admin_group in groups
    email = claims.get("email")
    display_name = claims.get("name") or claims.get("preferred_username")

    with SessionLocal() as db:
        user = db.execute(select(models.User).where(models.User.oidc_subject == subject)).scalar_one_or_none()
        if user is None:
            user = models.User(oidc_subject=subject)
            db.add(user)
        user.email = email
        user.display_name = display_name
        user.is_admin = is_admin
        user.last_login_at = datetime.now(UTC)
        db.commit()
        user_id = user.id

    session_id, data = create_session()
    data.user_id = user_id
    data.is_admin = is_admin
    data.display_name = display_name

    response = RedirectResponse(url="/")
    set_session_cookie(response, session_id)
    return response


@router.post("/auth/logout")
async def logout(request: Request):
    response = RedirectResponse(url="/", status_code=303)
    clear_session(request, response)
    return response
