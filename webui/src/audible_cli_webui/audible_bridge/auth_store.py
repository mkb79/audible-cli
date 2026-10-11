from datetime import UTC, datetime

from audible import Authenticator
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models
from ..crypto import decrypt_json, encrypt_json


def _extract_label(auth_dict: dict) -> str | None:
    customer_info = auth_dict.get("customer_info") or {}
    return customer_info.get("name") or customer_info.get("user_id")


def get_account(db: Session, user_id: int) -> models.AudibleAccount | None:
    return db.execute(
        select(models.AudibleAccount).where(models.AudibleAccount.user_id == user_id)
    ).scalar_one_or_none()


def save_account(
    db: Session,
    user_id: int,
    marketplace: str,
    with_username: bool,
    auth_dict: dict,
) -> models.AudibleAccount:
    account = get_account(db, user_id)
    if account is None:
        account = models.AudibleAccount(user_id=user_id)
        db.add(account)

    account.marketplace = marketplace
    account.with_username = with_username
    account.encrypted_auth_blob = encrypt_json(auth_dict)
    account.amazon_account_label = _extract_label(auth_dict)
    account.status = models.AudibleAccountStatus.ACTIVE
    account.last_refreshed_at = datetime.now(UTC)
    db.commit()
    db.refresh(account)
    return account


def load_authenticator(account: models.AudibleAccount) -> Authenticator:
    auth_dict = decrypt_json(account.encrypted_auth_blob)
    return Authenticator.from_dict(auth_dict, locale=account.marketplace)


def persist_refreshed_tokens(db: Session, account: models.AudibleAccount, authenticator: Authenticator) -> None:
    account.encrypted_auth_blob = encrypt_json(authenticator.to_dict())
    account.last_refreshed_at = datetime.now(UTC)
    db.commit()


def mark_needs_reconnect(db: Session, account: models.AudibleAccount) -> None:
    account.status = models.AudibleAccountStatus.NEEDS_RECONNECT
    db.commit()


def delete_account(db: Session, user_id: int) -> None:
    account = get_account(db, user_id)
    if account is not None:
        db.delete(account)
        db.commit()
