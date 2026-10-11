"""Where in Audiobookshelf a user's import lands, and whether they can import at all.

A user's own TenantLibraryMapping wins; otherwise the admin-set default on
AbsSettings applies. Either way the target must be a folder of a *book*
library — uploading audiobooks into a podcast library is never right.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models
from ..audible_bridge import auth_store

BOOK_MEDIA_TYPE = "book"


class InvalidTargetError(ValueError):
    """A library/folder choice that Audiobookshelf can't accept uploads into."""


def validate_target(libraries: list[dict], library_id: str, folder_id: str) -> None:
    library = next((lib for lib in libraries if lib["id"] == library_id), None)
    if library is None:
        raise InvalidTargetError("That Audiobookshelf library no longer exists.")
    if library["media_type"] != BOOK_MEDIA_TYPE:
        raise InvalidTargetError(
            f"'{library['name']}' is a {library['media_type']} library; audiobooks can only go into a book library."
        )
    if not any(folder["id"] == folder_id for folder in library["folders"]):
        raise InvalidTargetError(f"That folder isn't part of the '{library['name']}' library.")


def sole_book_target(libraries: list[dict]) -> tuple[str, str] | None:
    """The only sensible default when there's exactly one book library with one folder."""
    book_libraries = [lib for lib in libraries if lib["media_type"] == BOOK_MEDIA_TYPE]
    if len(book_libraries) == 1 and len(book_libraries[0]["folders"]) == 1:
        return book_libraries[0]["id"], book_libraries[0]["folders"][0]["id"]
    return None


def resolve_target(db: Session, user_id: int) -> tuple[str, str] | None:
    mapping = db.execute(
        select(models.TenantLibraryMapping).where(models.TenantLibraryMapping.user_id == user_id)
    ).scalar_one_or_none()
    if mapping is not None:
        return mapping.abs_library_id, mapping.abs_folder_id

    abs_settings = db.get(models.AbsSettings, 1)
    if abs_settings is not None and abs_settings.default_library_id and abs_settings.default_folder_id:
        return abs_settings.default_library_id, abs_settings.default_folder_id
    return None


def missing_setup_reason(db: Session, user_id: int) -> str | None:
    """A user-facing explanation of what's stopping this user's import, or None if nothing is."""
    if db.get(models.AbsSettings, 1) is None:
        return "Audiobookshelf isn't set up yet. Ask an admin to add the server on the Admin settings page."
    if auth_store.get_account(db, user_id) is None:
        return "Connect your Audible account before importing."
    if resolve_target(db, user_id) is None:
        return (
            "No Audiobookshelf library is chosen for your imports, and there's no default. "
            "Ask an admin to set a default library (or one for you) on the Admin settings page."
        )
    return None
