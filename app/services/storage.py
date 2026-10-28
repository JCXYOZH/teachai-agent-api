import hashlib
import os
import shutil
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Tuple
from uuid import UUID, uuid4

import aiofiles  # type: ignore[import-untyped]
from fastapi import UploadFile
from starlette.concurrency import run_in_threadpool

from app.core.config import Settings
from app.core.errors import AppError

ALLOWED_FILE_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".txt": "text/plain",
}


@dataclass(frozen=True)
class PendingUpload:
    temp_path: Path
    target_path: Path
    storage_key: str
    size_bytes: int
    sha256: str


class LocalAssetStorage:
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
                503,
                "STORAGE_UNAVAILABLE",
                "Asset storage is not available",
            ) from exc
        if not self.root.is_dir():
            raise AppError(
                503,
                "STORAGE_UNAVAILABLE",
                "Asset storage is not available",
            )

    async def save_upload(
        self,
        owner_id: UUID,
        asset_id: UUID,
        extension: str,
        upload: UploadFile,
    ) -> PendingUpload:
        if extension not in ALLOWED_FILE_TYPES:
            raise AppError(
                415,
                "UNSUPPORTED_FILE_TYPE",
                "Only PDF, DOCX, PPTX, Markdown and TXT files are supported",
            )

        self.ensure_root()
        asset_dir = self.root / str(owner_id) / str(asset_id)
        target_path = asset_dir / f"original{extension}"
        temp_path = asset_dir / f".upload-{uuid4().hex}.tmp"
        storage_key = f"{owner_id}/{asset_id}/{target_path.name}"
        digest = hashlib.sha256()
        total = 0

        try:
            asset_dir.mkdir(parents=True, exist_ok=False)
            async with aiofiles.open(temp_path, "wb") as output:
                while True:
                    chunk = await upload.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > self.settings.asset_max_bytes:
                        raise AppError(
                            413,
                            "FILE_TOO_LARGE",
                            "Uploaded file exceeds the configured size limit",
                        )
                    digest.update(chunk)
                    await output.write(chunk)
            if total == 0:
                raise AppError(422, "EMPTY_FILE", "Uploaded file is empty")
        except BaseException:
            await self.discard_pending(owner_id, asset_id)
            raise

        return PendingUpload(
            temp_path=temp_path,
            target_path=target_path,
            storage_key=storage_key,
            size_bytes=total,
            sha256=digest.hexdigest(),
        )

    def finalize(self, pending: PendingUpload) -> None:
        os.replace(str(pending.temp_path), str(pending.target_path))

    async def discard_pending(self, owner_id: UUID, asset_id: UUID) -> None:
        asset_dir = self.root / str(owner_id) / str(asset_id)
        if asset_dir.exists():
            await self._remove_tree(asset_dir)

    def asset_directory(self, owner_id: UUID, asset_id: UUID) -> Path:
        candidate = (self.root / str(owner_id) / str(asset_id)).resolve()
        root = self.root
        try:
            inside_root = (
                os.path.commonpath(
                    [os.path.normcase(str(root)), os.path.normcase(str(candidate))]
                )
                == os.path.normcase(str(root))
            )
        except ValueError:
            inside_root = False
        if not inside_root:
            raise AppError(404, "ASSET_FILE_NOT_FOUND", "Asset file was not found")
        return candidate

    async def purge_asset(self, owner_id: UUID, asset_id: UUID) -> bool:
        asset_dir = self.asset_directory(owner_id, asset_id)
        if not asset_dir.is_dir():
            return False
        await self._remove_tree(asset_dir)
        return True

    def resolve_download(self, owner_id: UUID, asset_id: UUID, storage_key: str) -> Path:
        parts = PurePosixPath(storage_key).parts
        if len(parts) != 3 or parts[0] != str(owner_id) or parts[1] != str(asset_id):
            raise AppError(404, "ASSET_FILE_NOT_FOUND", "Asset file was not found")
        if any(part in {"", ".", ".."} for part in parts):
            raise AppError(404, "ASSET_FILE_NOT_FOUND", "Asset file was not found")

        candidate = (self.root / Path(*parts)).resolve()
        root = self.root
        try:
            inside_root = (
                os.path.commonpath(
                    [os.path.normcase(str(root)), os.path.normcase(str(candidate))]
                )
                == os.path.normcase(str(root))
            )
        except ValueError:
            inside_root = False
        if not inside_root or not candidate.is_file():
            raise AppError(404, "ASSET_FILE_NOT_FOUND", "Asset file was not found")
        return candidate

    def readiness(self) -> Tuple[int, int]:
        self.ensure_root()
        probe = self.root / f".write-probe-{uuid4().hex}"
        try:
            probe.write_bytes(b"teachai")
        except OSError as exc:
            raise AppError(
                503,
                "STORAGE_UNAVAILABLE",
                "Asset storage is not writable",
            ) from exc
        finally:
            try:
                probe.unlink(missing_ok=True)
            except OSError:
                pass
        usage = shutil.disk_usage(str(self.root))
        return usage.free, usage.total

    async def _remove_tree(self, path: Path) -> None:
        await run_in_threadpool(shutil.rmtree, str(path), True)
