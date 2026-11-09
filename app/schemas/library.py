from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.text import normalize_user_text_input


class FolderCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=120)

    @field_validator("name", mode="before")
    @classmethod
    def normalize_name_input(cls, value: Any) -> Any:
        return normalize_user_text_input(value, single_line=True)


class FolderUpdate(FolderCreate):
    pass


class FolderResponse(BaseModel):
    id: UUID
    name: str
    course_count: int
    asset_count: int
    created_at: datetime
    updated_at: datetime


class CourseCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=200)
    description: Optional[str] = Field(default=None, max_length=2000)
    folder_id: Optional[UUID] = None

    @field_validator("title", mode="before")
    @classmethod
    def normalize_title_input(cls, value: Any) -> Any:
        return normalize_user_text_input(value, single_line=True)

    @field_validator("description", mode="before")
    @classmethod
    def normalize_description_input(cls, value: Any) -> Any:
        return normalize_user_text_input(value)

    @field_validator("description")
    @classmethod
    def empty_description_to_none(cls, value: Optional[str]) -> Optional[str]:
        return value or None


class CourseUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    title: Optional[str] = Field(default=None, min_length=1, max_length=200)
    description: Optional[str] = Field(default=None, max_length=2000)
    folder_id: Optional[UUID] = None

    @field_validator("title", mode="before")
    @classmethod
    def normalize_title_input(cls, value: Any) -> Any:
        return normalize_user_text_input(value, single_line=True)

    @field_validator("description", mode="before")
    @classmethod
    def normalize_description_input(cls, value: Any) -> Any:
        return normalize_user_text_input(value)

    @field_validator("description")
    @classmethod
    def empty_description_to_none(cls, value: Optional[str]) -> Optional[str]:
        return value or None


class CourseResponse(BaseModel):
    id: UUID
    title: str
    description: Optional[str]
    folder_id: Optional[UUID]
    folder_name: Optional[str]
    status: str
    asset_count: int
    created_at: datetime
    updated_at: datetime


class CourseListResponse(BaseModel):
    items: List[CourseResponse]
    total: int
    limit: int
    offset: int


class AssetUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    filename: Optional[str] = Field(default=None, min_length=1, max_length=255)
    course_id: Optional[UUID] = None
    folder_id: Optional[UUID] = None

    @field_validator("filename", mode="before")
    @classmethod
    def normalize_filename_input(cls, value: Any) -> Any:
        return normalize_user_text_input(value, single_line=True)

    @field_validator("filename")
    @classmethod
    def empty_filename_to_none(cls, value: Optional[str]) -> Optional[str]:
        return value or None


class AssetResponse(BaseModel):
    id: UUID
    filename: str
    original_filename: str
    extension: str
    media_type: str
    size_bytes: int
    page_count: Optional[int]
    char_count: int
    extraction_status: str
    course_id: Optional[UUID]
    course_title: Optional[str]
    folder_id: Optional[UUID]
    folder_name: Optional[str]
    excerpt: Optional[str] = None
    download_url: str
    created_at: datetime
    updated_at: datetime


class AssetDetailResponse(AssetResponse):
    extraction_error: Optional[str]
    truncated: bool
    text_preview: str
    sections: List[Dict[str, Any]]


class AssetListResponse(BaseModel):
    items: List[AssetResponse]
    total: int
    limit: int
    offset: int


class SearchCourseHit(BaseModel):
    id: UUID
    title: str
    description: Optional[str]
    folder_name: Optional[str]
    updated_at: datetime


class SearchAssetHit(BaseModel):
    id: UUID
    filename: str
    media_type: str
    folder_name: Optional[str]
    course_title: Optional[str]
    excerpt: Optional[str]
    updated_at: datetime


class SearchResponse(BaseModel):
    query: str
    total: int
    courses: List[SearchCourseHit]
    assets: List[SearchAssetHit]
