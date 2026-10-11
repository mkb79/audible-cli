from datetime import datetime

from pydantic import BaseModel, ConfigDict


class Me(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    display_name: str | None
    email: str | None
    is_admin: bool


class AudibleAccountInfo(BaseModel):
    connected: bool
    marketplace: str | None = None
    with_username: bool = False
    status: str | None = None
    connected_at: datetime | None = None


class StartConnectRequest(BaseModel):
    marketplace: str
    with_username: bool = False


class LoginUrlResponse(BaseModel):
    login_url: str


class CompleteConnectRequest(BaseModel):
    pasted_url: str


class ImportReadinessOut(BaseModel):
    ready: bool
    reason: str | None = None


class ImportRunItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    asin: str
    title: str
    status: str
    error_message: str | None
    started_at: datetime | None
    finished_at: datetime | None

    @classmethod
    def from_orm_item(cls, item) -> "ImportRunItemOut":
        return cls(
            id=item.id,
            asin=item.asin,
            title=item.title,
            status=item.status.value,
            error_message=item.error_message,
            started_at=item.started_at,
            finished_at=item.finished_at,
        )


class ImportRunOut(BaseModel):
    id: int
    user_id: int
    status: str
    requested_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    total_items: int | None
    error_message: str | None

    @classmethod
    def from_orm_run(cls, run) -> "ImportRunOut":
        return cls(
            id=run.id,
            user_id=run.user_id,
            status=run.status.value,
            requested_at=run.requested_at,
            started_at=run.started_at,
            finished_at=run.finished_at,
            total_items=run.total_items,
            error_message=run.error_message,
        )


class ImportRunDetailOut(ImportRunOut):
    items: list[ImportRunItemOut]

    @classmethod
    def from_orm_run_with_items(cls, run) -> "ImportRunDetailOut":
        base = ImportRunOut.from_orm_run(run)
        return cls(**base.model_dump(), items=[ImportRunItemOut.from_orm_item(i) for i in run.items])


class AbsSettingsOut(BaseModel):
    base_url: str
    api_key_set: bool
    default_library_id: str | None = None
    default_folder_id: str | None = None


class AbsSettingsIn(BaseModel):
    base_url: str
    # Blank keeps the stored key, so the URL can change without re-entering it.
    api_key: str = ""


class AbsLibraryFolderOut(BaseModel):
    id: str
    name: str


class AbsLibraryOut(BaseModel):
    id: str
    name: str
    media_type: str
    folders: list[AbsLibraryFolderOut]


class TenantMappingIn(BaseModel):
    abs_library_id: str
    abs_folder_id: str


class TenantUserOut(BaseModel):
    id: int
    display_name: str | None
    email: str | None
    is_admin: bool
    audible_connected: bool
    abs_library_id: str | None
    abs_folder_id: str | None
