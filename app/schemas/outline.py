from datetime import datetime
from typing import Any, Dict, List, Literal, Optional, Union
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

OutlineSceneType = Literal["slide", "quiz", "interactive", "pbl"]


def _clean_items(values: List[str], limit: int) -> List[str]:
    cleaned: List[str] = []
    seen = set()
    for value in values:
        normalized = value.strip()
        key = normalized.casefold()
        if not normalized or key in seen:
            continue
        seen.add(key)
        cleaned.append(normalized[:500])
        if len(cleaned) >= limit:
            break
    return cleaned


class OutlineSceneInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    id: Optional[str] = Field(default=None, max_length=100)
    type: OutlineSceneType
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    key_points: List[str] = Field(default_factory=list, max_length=8)
    teaching_objective: Optional[str] = Field(default=None, max_length=1000)
    estimated_duration: Optional[int] = Field(default=None, ge=1, le=240)
    order: Optional[int] = Field(default=None, ge=1, le=100)
    language_note: Optional[str] = Field(default=None, max_length=500)
    widget_type: Optional[str] = Field(default=None, max_length=80)
    widget_outline: Optional[Dict[str, Any]] = None
    quiz_config: Optional[Dict[str, Any]] = None
    pbl_config: Optional[Dict[str, Any]] = None
    interactive_config: Optional[Dict[str, Any]] = None
    suggested_image_ids: List[str] = Field(default_factory=list, max_length=50)
    media_generations: Optional[Any] = None

    @field_validator("key_points")
    @classmethod
    def normalize_key_points(cls, values: List[str]) -> List[str]:
        return _clean_items(values, 8)


class OutlineScene(BaseModel):
    id: str
    type: OutlineSceneType
    title: str
    description: str
    key_points: List[str]
    teaching_objective: Optional[str] = None
    estimated_duration: Optional[int] = None
    order: int
    language_note: Optional[str] = None
    widget_type: Optional[str] = None
    widget_outline: Optional[Union[Dict[str, Any], List[Any]]] = None
    quiz_config: Optional[Union[Dict[str, Any], List[Any]]] = None
    pbl_config: Optional[Union[Dict[str, Any], List[Any]]] = None
    interactive_config: Optional[Union[Dict[str, Any], List[Any]]] = None
    suggested_image_ids: Optional[List[str]] = None
    media_generations: Optional[Any] = None


class OutlineGenerateRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    provider_id: Optional[str] = Field(default=None, max_length=50)
    model: Optional[str] = Field(default=None, max_length=200)
    instructions: Optional[str] = Field(default=None, max_length=4000)
    scene_count: Optional[int] = Field(default=None, ge=3, le=20)

    @field_validator("instructions")
    @classmethod
    def normalize_instructions(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        normalized = value.strip()
        if any(ord(character) < 32 and character not in "\n\t" for character in normalized):
            raise ValueError("Instructions contain unsupported control characters")
        return normalized or None


class OutlineSaveRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    course_title: Optional[str] = Field(default=None, min_length=1, max_length=200)
    language_directive: Optional[str] = Field(default=None, max_length=2000)
    scenes: List[OutlineSceneInput] = Field(min_length=1, max_length=100)


class CourseOutlineResponse(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    id: UUID
    course_id: UUID
    version: int
    source: Literal["generated", "manual"]
    provider_id: Optional[str]
    model_name: Optional[str]
    language_directive: str
    course_title: str
    scenes: List[OutlineScene]
    scene_count: int
    material_asset_count: int
    material_char_count: int
    materials_truncated: bool
    prompt_version: str
    created_at: datetime
