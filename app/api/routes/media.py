import re
from typing import Dict
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.errors import AppError
from app.db.session import get_db
from app.models.media import MediaAsset
from app.models.user import User
from app.schemas.media import MediaAssetView
from app.services.media_storage import LocalMediaStorage

router = APIRouter(prefix="/media", tags=["media"])

UNSAFE_FILENAME_CHARS = re.compile(r"[^A-Za-z0-9._-]+")


def content_disposition(filename: str) -> str:
    ascii_name = UNSAFE_FILENAME_CHARS.sub("-", filename).strip("-")
    if not ascii_name:
        ascii_name = "classroom-export"
    return (
        f'attachment; filename="{ascii_name}"; '
        f"filename*=UTF-8''{quote(filename, safe='')}"
    )


def get_media_storage() -> LocalMediaStorage:
    return LocalMediaStorage(settings)


async def _get_asset(
    db: AsyncSession, owner_id: UUID, media_id: UUID
) -> MediaAsset:
    asset = await db.scalar(
        select(MediaAsset).where(
            MediaAsset.id == media_id,
            MediaAsset.owner_id == owner_id,
        )
    )
    if asset is None:
        raise AppError(404, "MEDIA_NOT_FOUND", "Media asset not found")
    return asset


@router.get("/{media_id}", response_model=MediaAssetView)
async def get_media_asset(
    media_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> MediaAssetView:
    asset = await _get_asset(db, current_user.id, media_id)
    return MediaAssetView(
        id=asset.id,
        kind=asset.kind,
        format=asset.format,
        mime_type=asset.mime_type,
        size_bytes=asset.size_bytes,
        duration_ms=asset.duration_ms,
        provider_id=asset.provider_id,
        model_name=asset.model_name,
        voice=asset.voice,
        course_id=asset.course_id,
        stage_id=asset.stage_id,
        scene_id=asset.scene_id,
        action_id=asset.action_id,
        content_url=f"{settings.api_v1_prefix}/media/{asset.id}/content",
        created_at=asset.created_at,
    )


@router.get("/{media_id}/content")
async def get_media_content(
    media_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    storage: LocalMediaStorage = Depends(get_media_storage),
) -> FileResponse:
    asset = await _get_asset(db, current_user.id, media_id)
    path = storage.resolve(current_user.id, asset.id, asset.storage_key)
    headers: Dict[str, str] = {
        "Cache-Control": "private, max-age=300",
        "ETag": f'"{asset.sha256}"',
        "X-Content-Type-Options": "nosniff",
    }
    if asset.kind == "export":
        metadata = asset.media_metadata if isinstance(asset.media_metadata, dict) else {}
        filename = str(metadata.get("filename") or f"classroom.{asset.format}")
        headers["Cache-Control"] = "private, no-store"
        headers["Content-Disposition"] = content_disposition(filename)
    return FileResponse(
        path,
        media_type=asset.mime_type,
        headers=headers,
    )
