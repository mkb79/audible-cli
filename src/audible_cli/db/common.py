from __future__ import annotations

import hashlib
from pathlib import Path
from audible import Authenticator
from audible_cli.config import Session

def db_path_for_session(session: Session, db_name: str) -> Path:
    """Build a stable DB filename under session.app_dir using user + locale hash."""
    auth: Authenticator = session.auth
    user_id = auth.customer_info.get("user_id")
    locale = auth.locale.country_code
    key = f"{user_id}#{locale}"
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
    app_dir = Path(session.app_dir)
    app_dir.mkdir(parents=True, exist_ok=True)
    return app_dir / f"{db_name}_{digest}.sqlite"
