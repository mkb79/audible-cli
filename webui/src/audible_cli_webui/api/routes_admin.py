from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models, schemas
from ..audible_bridge import auth_store
from ..crypto import decrypt_bytes, encrypt_bytes
from ..deps import get_db, require_admin
from ..importer.abs_client import AbsApiError, AbsClient

router = APIRouter(prefix="/api/admin", dependencies=[Depends(require_admin)])


def _get_abs_settings_row(db: Session) -> models.AbsSettings | None:
    return db.get(models.AbsSettings, 1)


@router.get("/abs-settings", response_model=schemas.AbsSettingsOut)
async def get_abs_settings(db: Session = Depends(get_db)) -> schemas.AbsSettingsOut:
    row = _get_abs_settings_row(db)
    if row is None:
        return schemas.AbsSettingsOut(base_url="", api_key_set=False)
    return schemas.AbsSettingsOut(base_url=row.base_url, api_key_set=True)


@router.put("/abs-settings", response_model=schemas.AbsSettingsOut)
async def save_abs_settings(
    body: schemas.AbsSettingsIn,
    user: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> schemas.AbsSettingsOut:
    try:
        await AbsClient(body.base_url, body.api_key).list_libraries()
    except AbsApiError as exc:
        raise HTTPException(status_code=400, detail=f"Could not validate Audiobookshelf settings: {exc}") from exc

    row = _get_abs_settings_row(db)
    if row is None:
        row = models.AbsSettings(id=1, base_url=body.base_url, encrypted_api_key=encrypt_bytes(body.api_key.encode()))
        db.add(row)
    else:
        row.base_url = body.base_url
        row.encrypted_api_key = encrypt_bytes(body.api_key.encode())
    row.updated_by_user_id = user.id
    db.commit()

    return schemas.AbsSettingsOut(base_url=row.base_url, api_key_set=True)


@router.get("/abs-libraries", response_model=list[schemas.AbsLibraryOut])
async def list_abs_libraries(db: Session = Depends(get_db)) -> list[dict]:
    row = _get_abs_settings_row(db)
    if row is None:
        raise HTTPException(status_code=400, detail="Audiobookshelf is not configured yet.")

    api_key = decrypt_bytes(row.encrypted_api_key).decode("utf-8")
    try:
        return await AbsClient(row.base_url, api_key).list_libraries()
    except AbsApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


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


@router.get("/imports", response_model=list[schemas.ImportRunOut])
async def admin_list_imports(user_id: int | None = None, db: Session = Depends(get_db)) -> list[schemas.ImportRunOut]:
    stmt = select(models.ImportRun).order_by(models.ImportRun.id.desc())
    if user_id is not None:
        stmt = stmt.where(models.ImportRun.user_id == user_id)
    runs = db.execute(stmt).scalars().all()
    return [schemas.ImportRunOut.from_orm_run(r) for r in runs]
