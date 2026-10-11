import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class AudibleAccountStatus(enum.StrEnum):
    ACTIVE = "active"
    NEEDS_RECONNECT = "needs_reconnect"


class ImportRunStatus(enum.StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ImportRunItemStatus(enum.StrEnum):
    PENDING = "pending"
    DOWNLOADING = "downloading"
    DECRYPTING = "decrypting"
    UPLOADING = "uploading"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    oidc_subject: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    email: Mapped[str | None] = mapped_column(String(255))
    display_name: Mapped[str | None] = mapped_column(String(255))
    # Cached from the `groups` claim on every login; never the authorization
    # source of truth at request time (recomputed, not trusted stale).
    is_admin: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    audible_account: Mapped["AudibleAccount | None"] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    library_mapping: Mapped["TenantLibraryMapping | None"] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    import_runs: Mapped[list["ImportRun"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class AudibleAccount(Base):
    __tablename__ = "audible_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True)
    marketplace: Mapped[str] = mapped_column(String(8))
    with_username: Mapped[bool] = mapped_column(default=False)
    # Fernet-encrypted JSON of exactly Authenticator.to_dict()'s shape.
    encrypted_auth_blob: Mapped[bytes] = mapped_column()
    status: Mapped[AudibleAccountStatus] = mapped_column(
        Enum(AudibleAccountStatus), default=AudibleAccountStatus.ACTIVE
    )
    amazon_account_label: Mapped[str | None] = mapped_column(String(255))
    connected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_refreshed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped["User"] = relationship(back_populates="audible_account")


class AbsSettings(Base):
    """Singleton row (id is always 1) holding the one shared Audiobookshelf target."""

    __tablename__ = "abs_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    base_url: Mapped[str] = mapped_column(String(500))
    encrypted_api_key: Mapped[bytes] = mapped_column()
    # Where imports land for users without their own TenantLibraryMapping.
    default_library_id: Mapped[str | None] = mapped_column(String(255))
    default_folder_id: Mapped[str | None] = mapped_column(String(255))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    updated_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))


class TenantLibraryMapping(Base):
    __tablename__ = "tenant_library_mappings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True)
    abs_library_id: Mapped[str] = mapped_column(String(255))
    abs_folder_id: Mapped[str] = mapped_column(String(255))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    user: Mapped["User"] = relationship(back_populates="library_mapping")


class ImportRun(Base):
    __tablename__ = "import_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    status: Mapped[ImportRunStatus] = mapped_column(Enum(ImportRunStatus), default=ImportRunStatus.QUEUED)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    total_items: Mapped[int | None] = mapped_column(Integer)
    error_message: Mapped[str | None] = mapped_column(Text)

    user: Mapped["User"] = relationship(back_populates="import_runs")
    items: Mapped[list["ImportRunItem"]] = relationship(
        back_populates="run", cascade="all, delete-orphan", order_by="ImportRunItem.id"
    )


class ImportRunItem(Base):
    __tablename__ = "import_run_items"
    __table_args__ = (UniqueConstraint("run_id", "asin", name="uq_import_run_items_run_asin"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("import_runs.id"), index=True)
    asin: Mapped[str] = mapped_column(String(32), index=True)
    title: Mapped[str] = mapped_column(String(500))
    status: Mapped[ImportRunItemStatus] = mapped_column(Enum(ImportRunItemStatus), default=ImportRunItemStatus.PENDING)
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Doubles as the idempotency marker: a prior row for this ASIN with
    # status SUCCEEDED means a later run skips re-importing it.
    abs_library_item_id: Mapped[str | None] = mapped_column(String(255))

    run: Mapped["ImportRun"] = relationship(back_populates="items")
