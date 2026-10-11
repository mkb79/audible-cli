from pathlib import Path

import httpx


class AbsApiError(Exception):
    """A user-facing failure talking to the Audiobookshelf server."""


class AbsClient:
    def __init__(self, base_url: str, api_key: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {api_key}"}

    async def list_libraries(self) -> list[dict]:
        async with httpx.AsyncClient(headers=self._headers, timeout=30) as client:
            try:
                resp = await client.get(f"{self._base_url}/api/libraries")
                resp.raise_for_status()
            except httpx.HTTPError as exc:
                raise AbsApiError(f"Could not reach Audiobookshelf: {exc}") from exc

        return [
            {
                "id": lib["id"],
                "name": lib["name"],
                # "book" or "podcast". Uploads only make sense into a book
                # library, so callers filter/validate on this.
                "media_type": lib.get("mediaType", "book"),
                "folders": [{"id": f["id"], "name": f.get("fullPath", f["id"])} for f in lib.get("folders", [])],
            }
            for lib in resp.json().get("libraries", [])
        ]

    async def upload_book(
        self,
        *,
        library_id: str,
        folder_id: str,
        title: str,
        author: str | None,
        series: str | None,
        audio_path: Path,
        cover_path: Path | None = None,
    ) -> None:
        """Upload one audiobook (+ optional cover) to Audiobookshelf.

        The endpoint's response body is just the plain-text string "OK" on
        success (verified against a live server) — it does not return the
        created library item's id, so this app's idempotency tracking
        relies entirely on its own import_run_items table, never on an
        Audiobookshelf-side id.
        """
        data = {"title": title, "library": library_id, "folder": folder_id}
        if author:
            data["author"] = author
        if series:
            data["series"] = series

        with audio_path.open("rb") as audio_file:
            files = {"0": (audio_path.name, audio_file, "audio/mp4")}
            cover_file = cover_path.open("rb") if cover_path and cover_path.is_file() else None
            if cover_file is not None:
                files["1"] = (cover_path.name, cover_file, "image/jpeg")

            try:
                async with httpx.AsyncClient(headers=self._headers, timeout=600) as client:
                    try:
                        resp = await client.post(f"{self._base_url}/api/upload", data=data, files=files)
                        resp.raise_for_status()
                    except httpx.HTTPError as exc:
                        detail = exc.response.text if isinstance(exc, httpx.HTTPStatusError) else str(exc)
                        raise AbsApiError(f"Audiobookshelf upload failed: {detail}") from exc
            finally:
                if cover_file is not None:
                    cover_file.close()
