from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.errors import AppError
from app.db.session import get_db
from app.models.user import User
from app.schemas.library import (
    AssetDetailResponse,
    AssetListResponse,
    AssetResponse,
    AssetUpdate,
    CourseCreate,
    CourseListResponse,
    CourseResponse,
    CourseUpdate,
    FolderCreate,
    FolderResponse,
    FolderUpdate,
    SearchResponse,
)
from app.services.library import LibraryService
from app.services.storage import LocalAssetStorage

folders_router = APIRouter(prefix="/folders", tags=["folders"])
courses_router = APIRouter(prefix="/courses", tags=["courses"])
assets_router = APIRouter(prefix="/assets", tags=["assets"])
search_router = APIRouter(prefix="/search", tags=["search"])


def get_library_service(db: AsyncSession = Depends(get_db)) -> LibraryService:
    return LibraryService(db, settings, LocalAssetStorage(settings))


@folders_router.get("", response_model=List[FolderResponse])
async def list_folders(
    current_user: User = Depends(get_current_user),
    service: LibraryService = Depends(get_library_service),
) -> List[FolderResponse]:
    return await service.list_folders(current_user)


@folders_router.post("", response_model=FolderResponse, status_code=status.HTTP_201_CREATED)
async def create_folder(
    payload: FolderCreate,
    current_user: User = Depends(get_current_user),
    service: LibraryService = Depends(get_library_service),
) -> FolderResponse:
    return await service.create_folder(current_user, payload.name)


@folders_router.patch("/{folder_id}", response_model=FolderResponse)
async def update_folder(
    folder_id: UUID,
    payload: FolderUpdate,
    current_user: User = Depends(get_current_user),
    service: LibraryService = Depends(get_library_service),
) -> FolderResponse:
    return await service.update_folder(current_user, folder_id, payload.name)


@folders_router.delete("/{folder_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_folder(
    folder_id: UUID,
    current_user: User = Depends(get_current_user),
    service: LibraryService = Depends(get_library_service),
) -> None:
    await service.delete_folder(current_user, folder_id)


@courses_router.get("", response_model=CourseListResponse)
async def list_courses(
    q: Optional[str] = Query(default=None, min_length=1, max_length=100),
    folder_id: Optional[UUID] = None,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    service: LibraryService = Depends(get_library_service),
) -> CourseListResponse:
    normalized = (q or '').strip() or None
    return await service.list_courses(
        current_user, normalized, folder_id, limit, offset
    )


@courses_router.post("", response_model=CourseResponse, status_code=status.HTTP_201_CREATED)
async def create_course(
    payload: CourseCreate,
    current_user: User = Depends(get_current_user),
    service: LibraryService = Depends(get_library_service),
) -> CourseResponse:
    return await service.create_course(
        current_user,
        payload.title,
        payload.description,
        payload.folder_id,
    )


@courses_router.get("/{course_id}", response_model=CourseResponse)
async def get_course(
    course_id: UUID,
    current_user: User = Depends(get_current_user),
    service: LibraryService = Depends(get_library_service),
) -> CourseResponse:
    return await service.get_course(current_user, course_id)


@courses_router.patch("/{course_id}", response_model=CourseResponse)
async def update_course(
    course_id: UUID,
    payload: CourseUpdate,
    current_user: User = Depends(get_current_user),
    service: LibraryService = Depends(get_library_service),
) -> CourseResponse:
    return await service.update_course(
        current_user,
        course_id,
        payload.model_dump(exclude_unset=True),
    )


@courses_router.delete("/{course_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_course(
    course_id: UUID,
    current_user: User = Depends(get_current_user),
    service: LibraryService = Depends(get_library_service),
) -> None:
    await service.delete_course(current_user, course_id)


@assets_router.get("", response_model=AssetListResponse)
async def list_assets(
    q: Optional[str] = Query(default=None, min_length=1, max_length=100),
    course_id: Optional[UUID] = None,
    folder_id: Optional[UUID] = None,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    service: LibraryService = Depends(get_library_service),
) -> AssetListResponse:
    normalized = (q or '').strip() or None
    return await service.list_assets(
        current_user, normalized, course_id, folder_id, limit, offset
    )


@assets_router.post("", response_model=AssetResponse, status_code=status.HTTP_201_CREATED)
async def upload_asset(
    file: UploadFile = File(...),
    course_id: Optional[UUID] = Form(default=None),
    folder_id: Optional[UUID] = Form(default=None),
    current_user: User = Depends(get_current_user),
    service: LibraryService = Depends(get_library_service),
) -> AssetResponse:
    return await service.upload_asset(current_user, file, course_id, folder_id)


@assets_router.get("/{asset_id}", response_model=AssetDetailResponse)
async def get_asset(
    asset_id: UUID,
    current_user: User = Depends(get_current_user),
    service: LibraryService = Depends(get_library_service),
) -> AssetDetailResponse:
    return await service.get_asset(current_user, asset_id)


@assets_router.patch("/{asset_id}", response_model=AssetResponse)
async def update_asset(
    asset_id: UUID,
    payload: AssetUpdate,
    current_user: User = Depends(get_current_user),
    service: LibraryService = Depends(get_library_service),
) -> AssetResponse:
    return await service.update_asset(
        current_user,
        asset_id,
        payload.model_dump(exclude_unset=True),
    )


@assets_router.get("/{asset_id}/download")
async def download_asset(
    asset_id: UUID,
    current_user: User = Depends(get_current_user),
    service: LibraryService = Depends(get_library_service),
) -> FileResponse:
    asset, path = await service.get_asset_file(current_user, asset_id)
    return FileResponse(
        path,
        media_type=asset.media_type,
        filename=asset.filename,
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@assets_router.delete("/{asset_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_asset(
    asset_id: UUID,
    current_user: User = Depends(get_current_user),
    service: LibraryService = Depends(get_library_service),
) -> None:
    await service.delete_asset(current_user, asset_id)


@search_router.get("", response_model=SearchResponse)
async def search_library(
    q: str = Query(min_length=1, max_length=100),
    limit: int = Query(default=8, ge=1, le=20),
    current_user: User = Depends(get_current_user),
    service: LibraryService = Depends(get_library_service),
) -> SearchResponse:
    normalized = q.strip()
    if not normalized:
        raise AppError(422, 'INVALID_SEARCH_QUERY', 'Search query must not be empty')
    return await service.search(current_user, normalized, limit)
