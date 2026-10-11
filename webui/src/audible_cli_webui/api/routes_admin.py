from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models, schemas
from ..audible_bridge import auth_store
from ..crypto import decrypt_bytes, encrypt_bytes
from ..deps import get_db, require_admin
from ..importer.abs_client import AbsApiError, AbsClient
from ..importer.targets import InvalidTargetError, sole_book_target, validate_target

router = APIRouter(prefix="/api/admin", dependencies=[Depends(require_admin)])


def _get_abs_settings_row(db: Session) -> models.AbsSettings | None:
    return db.get(models.AbsSettings, 1)


def _abs_settings_out(row: models.AbsSettings | None) -> schemas.AbsSettingsOut:
    if row is None:
        return schemas.AbsSettingsOut(base_url="", api_key_set=False)
    return schemas.AbsSettingsOut(
        base_url=row.base_url,
        api_key_set=True,
        default_library_id=row.default_library_id,
        default_folder_id=row.default_folder_id,
    )


async def _fetch_libraries(row: models.AbsSettings) -> list[dict]:
    api_key = decrypt_bytes(row.encrypted_api_key).decode("utf-8")
    try:
        return await AbsClient(row.base_url, api_key).list_libraries()
    except AbsApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


async def _validate_target_or_400(db: Session, library_id: str, folder_id: str) -> None:
    row = _get_abs_settings_row(db)
    if row is None:
        raise HTTPException(status_code=400, detail="Audiobookshelf is not configured yet.")
    try:
        validate_target(await _fetch_libraries(row), library_id, folder_id)
    except InvalidTargetError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/abs-settings", response_model=schemas.AbsSettingsOut)
async def get_abs_settings(db: Session = Depends(get_db)) -> schemas.AbsSettingsOut:
    return _abs_settings_out(_get_abs_settings_row(db))


@router.put("/abs-settings", response_model=schemas.AbsSettingsOut)
async def save_abs_settings(
    body: schemas.AbsSettingsIn,
    user: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> schemas.AbsSettingsOut:
    row = _get_abs_settings_row(db)
    api_key = body.api_key.strip()
    if not api_key:
        if row is None:
            raise HTTPException(status_code=400, detail="An API key is required the first time.")
        api_key = decrypt_bytes(row.encrypted_api_key).decode("utf-8")

    try:
        libraries = await AbsClient(body.base_url, api_key).list_libraries()
    except AbsApiError as exc:
        raise HTTPException(status_code=400, detail=f"Could not validate Audiobookshelf settings: {exc}") from exc

    if row is None:
        row = models.AbsSettings(id=1, base_url=body.base_url, encrypted_api_key=encrypt_bytes(api_key.encode()))
        db.add(row)
    else:
        row.base_url = body.base_url
        row.encrypted_api_key = encrypt_bytes(api_key.encode())
    row.updated_by_user_id = user.id

    # A default that no longer validates (library deleted, pointed at a
    # different server) is dropped rather than left to fail every import.
    if row.default_library_id and row.default_folder_id:
        try:
            validate_target(libraries, row.default_library_id, row.default_folder_id)
        except InvalidTargetError:
            row.default_library_id = row.default_folder_id = None
    if not row.default_library_id:
        target = sole_book_target(libraries)
        if target is not None:
            row.default_library_id, row.default_folder_id = target
    db.commit()

    return _abs_settings_out(row)


@router.put("/abs-default-target", response_model=schemas.AbsSettingsOut)
async def set_default_target(body: schemas.TenantMappingIn, db: Session = Depends(get_db)) -> schemas.AbsSettingsOut:
    await _validate_target_or_400(db, body.abs_library_id, body.abs_folder_id)
    row = _get_abs_settings_row(db)
    row.default_library_id = body.abs_library_id
    row.default_folder_id = body.abs_folder_id
    db.commit()
    return _abs_settings_out(row)


@router.get("/abs-libraries", response_model=list[schemas.AbsLibraryOut])
async def list_abs_libraries(db: Session = Depends(get_db)) -> list[dict]:
    row = _get_abs_settings_row(db)
    if row is None:
        raise HTTPException(status_code=400, detail="Audiobookshelf is not configured yet.")
    return await _fetch_libraries(row)


@router.get("/users", response_model=list[schemas.TenantUserOut])
async def list_users(db: Session = Depends(get_db)) -> list[schemas.TenantUserOut]:
    users = db.execute(select(models.User)).scalars().all()
    out = []
    for u in users:
        account = auth_store.get_account(db, u.id)
        mapping = db.execute(
            select(models.TenantLibraryMapping).where(models.TenantLibraryMapping.user_id == u.id)
        ).scalar_one_or_none()
        out.append(
            schemas.TenantUserOut(
                id=u.id,
                display_name=u.display_name,
                email=u.email,
                is_admin=u.is_admin,
                audible_connected=account is not None,
                abs_library_id=mapping.abs_library_id if mapping else None,
                abs_folder_id=mapping.abs_folder_id if mapping else None,
            )
        )
    return out


@router.put("/tenant-mappings/{user_id}")
async def set_tenant_mapping(
    user_id: int,
    body: schemas.TenantMappingIn,
    db: Session = Depends(get_db),
) -> dict:
    target_user = db.get(models.User, user_id)
    if target_user is None:
        raise HTTPException(status_code=404, detail="User not found.")
    await _validate_target_or_400(db, body.abs_library_id, body.abs_folder_id)

    mapping = db.execute(
        select(models.TenantLibraryMapping).where(models.TenantLibraryMapping.user_id == user_id)
    ).scalar_one_or_none()
    if mapping is None:
        mapping = models.TenantLibraryMapping(user_id=user_id)
        db.add(mapping)
    mapping.abs_library_id = body.abs_library_id
    mapping.abs_folder_id = body.abs_folder_id
    db.commit()
    return {"ok": True}


@router.delete("/tenant-mappings/{user_id}")
async def clear_tenant_mapping(user_id: int, db: Session = Depends(get_db)) -> dict:
    """Drop a user's own library choice so their imports fall back to the default."""
    mapping = db.execute(
        select(models.TenantLibraryMapping).where(models.TenantLibraryMapping.user_id == user_id)
    ).scalar_one_or_none()
    if mapping is not None:
        db.delete(mapping)
        db.commit()
    return {"ok": True}


@router.get("/imports", response_model=list[schemas.ImportRunOut])
async def admin_list_imports(user_id: int | None = None, db: Session = Depends(get_db)) -> list[schemas.ImportRunOut]:
    stmt = select(models.ImportRun).order_by(models.ImportRun.id.desc())
    if user_id is not None:
        stmt = stmt.where(models.ImportRun.user_id == user_id)
    runs = db.execute(stmt).scalars().all()
    return [schemas.ImportRunOut.from_orm_run(r) for r in runs]
