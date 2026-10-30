import hashlib
import os
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from uuid import UUID, uuid4

from starlette.concurrency import run_in_threadpool

from app.core.config import Settings
from app.core.errors import AppError


@dataclass(frozen=True)
class SavedMedia:
    storage_key: str
    size_bytes: int
    sha256: str


class LocalMediaStorage:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @property
    def root(self) -> Path:
        return self.settings.asset_storage_root.resolve()

    def ensure_root(self) -> None:
        try:
            self.root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise AppError(
                503, "STORAGE_UNAVAILABLE", "Media storage is not available"
            ) from exc
        if not self.root.is_dir():
            raise AppError(
                503, "STORAGE_UNAVAILABLE", "Media storage is not available"
            )

    def media_directory(self, owner_id: UUID, media_id: UUID) -> Path:
        candidate = (self.root / str(owner_id) / "media" / str(media_id)).resolve()
        if not self._inside_root(candidate):
            raise AppError(404, "MEDIA_FILE_NOT_FOUND", "Media file was not found")
        return candidate

    async def save_bytes(
        self,
        owner_id: UUID,
        media_id: UUID,
        stem: str,
        extension: str,
        data: bytes,
    ) -> SavedMedia:
        if not stem.isalnum() or not extension.isalnum():
            raise AppError(500, "MEDIA_PATH_INVALID", "Media path is invalid")
        if not data:
            raise AppError(502, "MEDIA_EMPTY", "Provider returned empty media")
        self.ensure_root()
        directory = self.media_directory(owner_id, media_id)
        filename = f"{stem}.{extension.lower()}"
        target = directory / filename
        temp = directory / f".write-{uuid4().hex}.tmp"

        def _write() -> None:
            directory.mkdir(parents=True, exist_ok=True)
            temp.write_bytes(data)
            os.replace(str(temp), str(target))

        try:
            await run_in_threadpool(_write)
        except OSError as exc:
            try:
                if temp.exists():
                    temp.unlink()
            except OSError:
                pass
            raise AppError(
                503, "STORAGE_UNAVAILABLE", "Media could not be written to storage"
            ) from exc
        return SavedMedia(
            storage_key=f"{owner_id}/media/{media_id}/{filename}",
            size_bytes=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
        )

    def resolve(self, owner_id: UUID, media_id: UUID, storage_key: str) -> Path:
        parts = PurePosixPath(storage_key).parts
        if (
            len(parts) != 4
            or parts[0] != str(owner_id)
            or parts[1] != "media"
            or parts[2] != str(media_id)
            or any(part in {"", ".", ".."} for part in parts)
        ):
            raise AppError(404, "MEDIA_FILE_NOT_FOUND", "Media file was not found")
        candidate = (self.root / Path(*parts)).resolve()
        if not self._inside_root(candidate) or not candidate.is_file():
            raise AppError(404, "MEDIA_FILE_NOT_FOUND", "Media file was not found")
        return candidate

    def exists(self, owner_id: UUID, media_id: UUID, storage_key: str) -> bool:
        try:
            return self.resolve(owner_id, media_id, storage_key).is_file()
        except AppError:
            return False

    def _inside_root(self, candidate: Path) -> bool:
        try:
            return (
                os.path.commonpath(
                    [os.path.normcase(str(self.root)), os.path.normcase(str(candidate))]
                )
                == os.path.normcase(str(self.root))
            )
        except ValueError:
            return False
