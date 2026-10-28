import asyncio
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID, uuid4

from fastapi import UploadFile
from sqlalchemy import case, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.core.config import Settings
from app.core.errors import AppError
from app.core.time import utcnow
from app.models.asset import Asset, AssetExtraction
from app.models.course import Course
from app.models.folder import Folder
from app.models.user import User
from app.schemas.library import (
    AssetDetailResponse,
    AssetListResponse,
    AssetResponse,
    CourseListResponse,
    CourseResponse,
    FolderResponse,
    SearchAssetHit,
    SearchCourseHit,
    SearchResponse,
)
from app.services.extraction import ExtractionError, extract_document
from app.services.storage import ALLOWED_FILE_TYPES, LocalAssetStorage


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _like_pattern(value: str) -> str:
    return f"%{_escape_like(value)}%"


def _clean_filename(value: str, extension: str) -> str:
    basename = value.replace("\\", "/").rsplit("/", 1)[-1]
    basename = "".join(
        character
        for character in basename
        if ord(character) >= 32 and character not in {'"', "*", ":", "<", ">", "?", "|"}
    ).strip()
    if not basename or basename in {".", ".."}:
        basename = f"untitled{extension}"
    suffix = Path(basename).suffix.lower()
    accepted = {extension}
    if extension == ".md":
        accepted.add(".markdown")
    if suffix not in accepted:
        basename = f"{Path(basename).stem or 'untitled'}{extension}"
    if len(basename) > 255:
        stem = Path(basename).stem[: 255 - len(Path(basename).suffix)]
        basename = f"{stem}{Path(basename).suffix}"
    return basename


class LibraryService:
    def __init__(
        self,
        db: AsyncSession,
        settings: Settings,
        storage: LocalAssetStorage,
    ) -> None:
        self.db = db
        self.settings = settings
        self.storage = storage

    async def list_folders(self, owner: User) -> List[FolderResponse]:
        course_count = (
            select(func.count(Course.id))
            .where(Course.folder_id == Folder.id, Course.deleted_at.is_(None))
            .correlate(Folder)
            .scalar_subquery()
        )
        asset_count = (
            select(func.count(Asset.id))
            .where(Asset.folder_id == Folder.id, Asset.deleted_at.is_(None))
            .correlate(Folder)
            .scalar_subquery()
        )
        result = await self.db.execute(
            select(Folder, course_count, asset_count)
            .where(Folder.owner_id == owner.id)
            .order_by(func.lower(Folder.name), Folder.created_at)
        )
        return [
            FolderResponse(
                id=folder.id,
                name=folder.name,
                course_count=int(courses or 0),
                asset_count=int(assets or 0),
                created_at=folder.created_at,
                updated_at=folder.updated_at,
            )
            for folder, courses, assets in result.all()
        ]

    async def create_folder(self, owner: User, name: str) -> FolderResponse:
        name_key = name.casefold()
        await self._ensure_folder_name_available(owner.id, name_key)
        folder = Folder(owner_id=owner.id, name=name, name_key=name_key)
        self.db.add(folder)
        try:
            await self.db.commit()
        except IntegrityError as exc:
            await self.db.rollback()
            raise AppError(409, "FOLDER_NAME_EXISTS", "A folder with this name exists") from exc
        await self.db.refresh(folder)
        return FolderResponse(
            id=folder.id,
            name=folder.name,
            course_count=0,
            asset_count=0,
            created_at=folder.created_at,
            updated_at=folder.updated_at,
        )

    async def update_folder(self, owner: User, folder_id: UUID, name: str) -> FolderResponse:
        folder = await self._get_folder(owner.id, folder_id)
        name_key = name.casefold()
        await self._ensure_folder_name_available(owner.id, name_key, folder_id)
        folder.name = name
        folder.name_key = name_key
        try:
            await self.db.commit()
        except IntegrityError as exc:
            await self.db.rollback()
            raise AppError(409, "FOLDER_NAME_EXISTS", "A folder with this name exists") from exc
        await self.db.refresh(folder)
        course_count, asset_count = await self._folder_counts(folder.id)
        return FolderResponse(
            id=folder.id,
            name=folder.name,
            course_count=course_count,
            asset_count=asset_count,
            created_at=folder.created_at,
            updated_at=folder.updated_at,
        )

    async def delete_folder(self, owner: User, folder_id: UUID) -> None:
        folder = await self._get_folder(owner.id, folder_id)
        await self.db.execute(
            update(Course)
            .where(Course.owner_id == owner.id, Course.folder_id == folder.id)
            .values(folder_id=None, updated_at=utcnow())
        )
        await self.db.execute(
            update(Asset)
            .where(Asset.owner_id == owner.id, Asset.folder_id == folder.id)
            .values(folder_id=None, updated_at=utcnow())
        )
        await self.db.delete(folder)
        await self.db.commit()

    async def list_courses(
        self,
        owner: User,
        query: Optional[str],
        folder_id: Optional[UUID],
        limit: int,
        offset: int,
    ) -> CourseListResponse:
        conditions = [Course.owner_id == owner.id, Course.deleted_at.is_(None)]
        if folder_id is not None:
            conditions.append(Course.folder_id == folder_id)
        if query:
            pattern = _like_pattern(query)
            conditions.append(
                or_(
                    Course.title.ilike(pattern, escape="\\"),
                    func.coalesce(Course.description, "").ilike(pattern, escape="\\"),
                )
            )

        asset_count = (
            select(func.count(Asset.id))
            .where(Asset.course_id == Course.id, Asset.deleted_at.is_(None))
            .correlate(Course)
            .scalar_subquery()
        )
        folder_name = (
            select(Folder.name)
            .where(Folder.id == Course.folder_id)
            .correlate(Course)
            .scalar_subquery()
        )
        total = await self.db.scalar(
            select(func.count()).select_from(Course).where(*conditions)
        )
        result = await self.db.execute(
            select(Course, folder_name, asset_count)
            .where(*conditions)
            .order_by(Course.updated_at.desc(), Course.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        items = [
            self._course_response(course, folder, int(assets or 0))
            for course, folder, assets in result.all()
        ]
        return CourseListResponse(
            items=items,
            total=int(total or 0),
            limit=limit,
            offset=offset,
        )

    async def get_course(self, owner: User, course_id: UUID) -> CourseResponse:
        course = await self._get_course(owner.id, course_id)
        folder_name = None
        if course.folder_id:
            folder = await self.db.get(Folder, course.folder_id)
            folder_name = folder.name if folder and folder.owner_id == owner.id else None
        asset_count = await self.db.scalar(
            select(func.count(Asset.id)).where(
                Asset.owner_id == owner.id,
                Asset.course_id == course.id,
                Asset.deleted_at.is_(None),
            )
        )
        return self._course_response(course, folder_name, int(asset_count or 0))

    async def create_course(
        self,
        owner: User,
        title: str,
        description: Optional[str],
        folder_id: Optional[UUID],
    ) -> CourseResponse:
        folder_name = await self._validate_folder_reference(owner.id, folder_id)
        course = Course(
            owner_id=owner.id,
            title=title,
            description=description,
            folder_id=folder_id,
        )
        self.db.add(course)
        await self.db.commit()
        await self.db.refresh(course)
        return self._course_response(course, folder_name, 0)

    async def update_course(
        self,
        owner: User,
        course_id: UUID,
        changes: Dict[str, Any],
    ) -> CourseResponse:
        course = await self._get_course(owner.id, course_id)
        if "title" in changes and changes["title"] is not None:
            course.title = changes["title"]
        if "description" in changes:
            course.description = changes["description"]
        if "folder_id" in changes:
            await self._validate_folder_reference(owner.id, changes["folder_id"])
            course.folder_id = changes["folder_id"]
        await self.db.commit()
        await self.db.refresh(course)
        return await self.get_course(owner, course.id)

    async def delete_course(self, owner: User, course_id: UUID) -> None:
        course = await self._get_course(owner.id, course_id)
        now = utcnow()
        course.deleted_at = now
        course.updated_at = now
        await self.db.execute(
            update(Asset)
            .where(Asset.owner_id == owner.id, Asset.course_id == course.id)
            .values(course_id=None, updated_at=now)
        )
        await self.db.commit()

    async def upload_asset(
        self,
        owner: User,
        upload: UploadFile,
        course_id: Optional[UUID],
        folder_id: Optional[UUID],
    ) -> AssetResponse:
        owner_id = owner.id
        original_name = upload.filename or ""
        extension = Path(original_name).suffix.lower()
        if extension not in ALLOWED_FILE_TYPES:
            raise AppError(
                415,
                "UNSUPPORTED_FILE_TYPE",
                "Only PDF, DOCX, PPTX, Markdown and TXT files are supported",
            )
        await self._validate_course_reference(owner_id, course_id)
        folder_name = await self._validate_folder_reference(owner_id, folder_id)
        course_title = await self._course_title(owner_id, course_id)
        filename = _clean_filename(original_name, extension)
        asset_id = uuid4()

        try:
            pending = await self.storage.save_upload(
                owner_id=owner_id,
                asset_id=asset_id,
                extension=extension,
                upload=upload,
            )
            try:
                extracted = await asyncio.wait_for(
                    run_in_threadpool(
                        extract_document,
                        pending.temp_path,
                        extension,
                        self.settings.asset_max_extracted_chars,
                    ),
                    timeout=self.settings.asset_parse_timeout_seconds,
                )
            except asyncio.TimeoutError as exc:
                raise AppError(
                    422,
                    "FILE_PARSE_TIMEOUT",
                    "The document took too long to parse",
                ) from exc
            except ExtractionError as exc:
                raise AppError(422, "FILE_PARSE_FAILED", str(exc)) from exc

            await run_in_threadpool(self.storage.finalize, pending)
            status = "ready" if extracted.text else "empty"
            asset = Asset(
                id=asset_id,
                owner_id=owner_id,
                course_id=course_id,
                folder_id=folder_id,
                filename=filename,
                original_filename=filename,
                extension=extension,
                media_type=ALLOWED_FILE_TYPES[extension],
                size_bytes=pending.size_bytes,
                sha256=pending.sha256,
                storage_key=pending.storage_key,
                extraction_status=status,
                page_count=extracted.page_count,
                char_count=len(extracted.text),
            )
            extraction = AssetExtraction(
                asset_id=asset_id,
                text_content=extracted.text,
                sections=extracted.sections,
                page_count=extracted.page_count,
                char_count=len(extracted.text),
                truncated=extracted.truncated,
            )
            self.db.add(asset)
            self.db.add(extraction)
            await self.db.commit()
        except BaseException:
            await self.db.rollback()
            await self.storage.discard_pending(owner_id, asset_id)
            raise
        finally:
            await upload.close()

        return self._asset_response(asset, folder_name, course_title)

    async def list_assets(
        self,
        owner: User,
        query: Optional[str],
        course_id: Optional[UUID],
        folder_id: Optional[UUID],
        limit: int,
        offset: int,
    ) -> AssetListResponse:
        conditions = [Asset.owner_id == owner.id, Asset.deleted_at.is_(None)]
        if course_id is not None:
            conditions.append(Asset.course_id == course_id)
        if folder_id is not None:
            conditions.append(Asset.folder_id == folder_id)
        if query:
            pattern = _like_pattern(query)
            conditions.append(
                or_(
                    Asset.filename.ilike(pattern, escape="\\"),
                    AssetExtraction.text_content.ilike(pattern, escape="\\"),
                )
            )

        folder_name = (
            select(Folder.name)
            .where(Folder.id == Asset.folder_id)
            .correlate(Asset)
            .scalar_subquery()
        )
        course_title = (
            select(Course.title)
            .where(Course.id == Asset.course_id)
            .correlate(Asset)
            .scalar_subquery()
        )
        excerpt = None
        if query:
            position = func.strpos(
                func.lower(AssetExtraction.text_content), query.lower()
            )
            excerpt = case(
                (
                    position > 0,
                    func.substr(
                        AssetExtraction.text_content,
                        func.greatest(position - 40, 1),
                        220,
                    ),
                ),
                else_=func.substr(AssetExtraction.text_content, 1, 220),
            )

        count_statement = (
            select(func.count(Asset.id))
            .select_from(Asset)
            .outerjoin(AssetExtraction, AssetExtraction.asset_id == Asset.id)
            .where(*conditions)
        )
        total = await self.db.scalar(count_statement)
        statement = (
            select(Asset, folder_name, course_title)
            .outerjoin(AssetExtraction, AssetExtraction.asset_id == Asset.id)
            .where(*conditions)
            .order_by(Asset.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        if excerpt is not None:
            statement = statement.add_columns(excerpt.label("excerpt"))
        result = await self.db.execute(statement)
        items = []
        for row in result.all():
            asset = row[0]
            items.append(
                self._asset_response(
                    asset,
                    row[1],
                    row[2],
                    row[3] if excerpt is not None else None,
                )
            )
        return AssetListResponse(
            items=items,
            total=int(total or 0),
            limit=limit,
            offset=offset,
        )

    async def get_asset(self, owner: User, asset_id: UUID) -> AssetDetailResponse:
        result = await self.db.execute(
            select(Asset, AssetExtraction)
            .outerjoin(AssetExtraction, AssetExtraction.asset_id == Asset.id)
            .where(
                Asset.id == asset_id,
                Asset.owner_id == owner.id,
                Asset.deleted_at.is_(None),
            )
        )
        row = result.one_or_none()
        if row is None:
            raise AppError(404, "ASSET_NOT_FOUND", "Asset was not found")
        asset, extraction = row
        folder_name = await self._folder_name(owner.id, asset.folder_id)
        course_title = await self._course_title(owner.id, asset.course_id)
        base = self._asset_response(asset, folder_name, course_title)
        sections: List[Dict[str, Any]] = []
        if extraction is not None:
            for section in (extraction.sections or [])[:100]:
                text = str(section.get("text", ""))
                sections.append(
                    {
                        "locator": section.get("locator", {}),
                        "text": text[:400],
                        "char_count": section.get("char_count", len(text)),
                    }
                )
        return AssetDetailResponse(
            **base.model_dump(),
            extraction_error=asset.extraction_error,
            truncated=bool(extraction.truncated) if extraction else False,
            text_preview=(extraction.text_content[:4000] if extraction else ""),
            sections=sections,
        )

    async def update_asset(
        self,
        owner: User,
        asset_id: UUID,
        changes: Dict[str, Any],
    ) -> AssetResponse:
        asset = await self._get_asset(owner.id, asset_id)
        if "filename" in changes and changes["filename"] is not None:
            asset.filename = _clean_filename(changes["filename"], asset.extension)
        if "course_id" in changes:
            await self._validate_course_reference(owner.id, changes["course_id"])
            asset.course_id = changes["course_id"]
        if "folder_id" in changes:
            await self._validate_folder_reference(owner.id, changes["folder_id"])
            asset.folder_id = changes["folder_id"]
        await self.db.commit()
        await self.db.refresh(asset)
        folder_name = await self._folder_name(owner.id, asset.folder_id)
        course_title = await self._course_title(owner.id, asset.course_id)
        return self._asset_response(asset, folder_name, course_title)

    async def delete_asset(self, owner: User, asset_id: UUID) -> None:
        asset = await self._get_asset(owner.id, asset_id)
        asset.deleted_at = utcnow()
        await self.db.commit()

    async def get_asset_file(self, owner: User, asset_id: UUID) -> Tuple[Asset, Path]:
        asset = await self._get_asset(owner.id, asset_id)
        path = await run_in_threadpool(
            self.storage.resolve_download,
            owner.id,
            asset.id,
            asset.storage_key,
        )
        return asset, path

    async def search(self, owner: User, query: str, limit: int) -> SearchResponse:
        courses = await self.list_courses(owner, query, None, limit, 0)
        assets = await self.list_assets(owner, query, None, None, limit, 0)
        course_hits = [
            SearchCourseHit(
                id=item.id,
                title=item.title,
                description=item.description,
                folder_name=item.folder_name,
                updated_at=item.updated_at,
            )
            for item in courses.items
        ]
        asset_hits = [
            SearchAssetHit(
                id=item.id,
                filename=item.filename,
                media_type=item.media_type,
                folder_name=item.folder_name,
                course_title=item.course_title,
                excerpt=item.excerpt,
                updated_at=item.updated_at,
            )
            for item in assets.items
        ]
        return SearchResponse(
            query=query,
            total=courses.total + assets.total,
            courses=course_hits,
            assets=asset_hits,
        )

    async def _get_folder(self, owner_id: UUID, folder_id: UUID) -> Folder:
        folder = await self.db.scalar(
            select(Folder).where(Folder.id == folder_id, Folder.owner_id == owner_id)
        )
        if folder is None:
            raise AppError(404, "FOLDER_NOT_FOUND", "Folder was not found")
        return folder

    async def _get_course(self, owner_id: UUID, course_id: UUID) -> Course:
        course = await self.db.scalar(
            select(Course).where(
                Course.id == course_id,
                Course.owner_id == owner_id,
                Course.deleted_at.is_(None),
            )
        )
        if course is None:
            raise AppError(404, "COURSE_NOT_FOUND", "Course was not found")
        return course

    async def _get_asset(self, owner_id: UUID, asset_id: UUID) -> Asset:
        asset = await self.db.scalar(
            select(Asset).where(
                Asset.id == asset_id,
                Asset.owner_id == owner_id,
                Asset.deleted_at.is_(None),
            )
        )
        if asset is None:
            raise AppError(404, "ASSET_NOT_FOUND", "Asset was not found")
        return asset

    async def _ensure_folder_name_available(
        self,
        owner_id: UUID,
        name_key: str,
        exclude_id: Optional[UUID] = None,
    ) -> None:
        statement = select(Folder.id).where(
            Folder.owner_id == owner_id, Folder.name_key == name_key
        )
        if exclude_id is not None:
            statement = statement.where(Folder.id != exclude_id)
        if await self.db.scalar(statement) is not None:
            raise AppError(409, "FOLDER_NAME_EXISTS", "A folder with this name exists")

    async def _validate_folder_reference(
        self, owner_id: UUID, folder_id: Optional[UUID]
    ) -> Optional[str]:
        if folder_id is None:
            return None
        folder = await self._get_folder(owner_id, folder_id)
        return folder.name

    async def _validate_course_reference(
        self, owner_id: UUID, course_id: Optional[UUID]
    ) -> None:
        if course_id is not None:
            await self._get_course(owner_id, course_id)

    async def _folder_name(self, owner_id: UUID, folder_id: Optional[UUID]) -> Optional[str]:
        if folder_id is None:
            return None
        folder = await self.db.scalar(
            select(Folder).where(Folder.id == folder_id, Folder.owner_id == owner_id)
        )
        return folder.name if folder else None

    async def _course_title(self, owner_id: UUID, course_id: Optional[UUID]) -> Optional[str]:
        if course_id is None:
            return None
        course = await self.db.scalar(
            select(Course).where(
                Course.id == course_id,
                Course.owner_id == owner_id,
                Course.deleted_at.is_(None),
            )
        )
        return course.title if course else None

    async def _folder_counts(self, folder_id: UUID) -> Tuple[int, int]:
        courses = await self.db.scalar(
            select(func.count(Course.id)).where(
                Course.folder_id == folder_id, Course.deleted_at.is_(None)
            )
        )
        assets = await self.db.scalar(
            select(func.count(Asset.id)).where(
                Asset.folder_id == folder_id, Asset.deleted_at.is_(None)
            )
        )
        return int(courses or 0), int(assets or 0)

    def _course_response(
        self,
        course: Course,
        folder_name: Optional[str],
        asset_count: int,
    ) -> CourseResponse:
        return CourseResponse(
            id=course.id,
            title=course.title,
            description=course.description,
            folder_id=course.folder_id,
            folder_name=folder_name,
            status=course.status,
            asset_count=asset_count,
            created_at=course.created_at,
            updated_at=course.updated_at,
        )

    def _asset_response(
        self,
        asset: Asset,
        folder_name: Optional[str],
        course_title: Optional[str],
        excerpt: Optional[str] = None,
    ) -> AssetResponse:
        cleaned_excerpt = re.sub(r"\s+", " ", excerpt or "").strip() or None
        return AssetResponse(
            id=asset.id,
            filename=asset.filename,
            original_filename=asset.original_filename,
            extension=asset.extension,
            media_type=asset.media_type,
            size_bytes=asset.size_bytes,
            page_count=asset.page_count,
            char_count=asset.char_count,
            extraction_status=asset.extraction_status,
            course_id=asset.course_id,
            course_title=course_title,
            folder_id=asset.folder_id,
            folder_name=folder_name,
            excerpt=cleaned_excerpt,
            download_url=f"{self.settings.api_v1_prefix}/assets/{asset.id}/download",
            created_at=asset.created_at,
            updated_at=asset.updated_at,
        )
