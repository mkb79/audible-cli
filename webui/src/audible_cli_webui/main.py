import logging
import shutil
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from starlette.middleware.sessions import SessionMiddleware

from .api import routes_admin, routes_audible, routes_auth, routes_imports
from .auth import oidc
from .importer.jobs import recover_interrupted_runs
from .settings import settings

logger = logging.getLogger("audible_cli_webui")


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    if shutil.which("ffmpeg") is None:
        raise RuntimeError(
            "ffmpeg was not found on PATH. It is required to decrypt and transcode downloaded audiobooks."
        )
    Path(settings.work_dir).mkdir(parents=True, exist_ok=True)
    recover_interrupted_runs()
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="audible-cli-webui", lifespan=lifespan)

    # Scoped narrowly to Authlib's own OAuth-handshake state (the `state`/
    # `nonce` round-trip to Authelia and back) — short-lived and separate
    # from this app's own server-side session (auth/session.py), which is
    # deliberately NOT stored in a client-side cookie.
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret,
        session_cookie="oauth_handshake",
        max_age=600,
        same_site="lax",
        https_only=settings.cookie_secure,
    )

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(oidc.router)
    app.include_router(routes_auth.router)
    app.include_router(routes_audible.router)
    app.include_router(routes_imports.router)
    app.include_router(routes_admin.router)

    # Populated by the frontend's build output in the Docker image; absent
    # in local dev, where Vite's own dev server serves the frontend instead.
    # Registered last: it's a catch-all and would otherwise shadow the
    # routers registered above.
    static_dir = Path(__file__).resolve().parent / "static"
    if static_dir.is_dir():

        @app.get("/{full_path:path}")
        async def serve_spa(full_path: str) -> FileResponse:
            # A React Router path like /connect-audible has no matching
            # file on disk — only index.html does, for every one of
            # these, so client-side routing can take over from there.
            # Without this, a hard refresh/deep-link on any route but "/"
            # 404s instead of rendering the app.
            candidate = static_dir / full_path
            if full_path and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(static_dir / "index.html")

    return app


app = create_app()
