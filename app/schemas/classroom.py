from datetime import datetime
from typing import Any, Dict, List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

SceneType = Literal["slide", "quiz", "interactive", "pbl"]
JobStatus = Literal["queued", "running", "completed", "failed"]


class ClassroomGenerateRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    provider_id: Optional[str] = Field(default=None, max_length=50)
    model: Optional[str] = Field(default=None, max_length=200)
    instructions: Optional[str] = Field(default=None, max_length=4000)

    @field_validator("instructions")
    @classmethod
    def normalize_instructions(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        normalized = value.strip()
        if any(ord(character) < 32 and character not in "\n\t" for character in normalized):
            raise ValueError("Instructions contain unsupported control characters")
        return normalized or None


class GenerationJobView(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    id: UUID
    course_id: UUID
    outline_id: Optional[UUID]
    stage_id: Optional[UUID]
    provider_id: Optional[str]
    model_name: Optional[str]
    status: JobStatus
    progress: int
    message: str
    error_code: Optional[str]
    error_message: Optional[str]
    scene_count: int
    created_at: datetime
    started_at: Optional[datetime]
    completed_at: Optional[datetime]


class SceneView(BaseModel):
    id: UUID
    stage_id: UUID
    type: SceneType
    title: str
    order: int
    content: Dict[str, Any]
    actions: List[Dict[str, Any]]
    created_at: datetime
    updated_at: datetime


class ClassroomView(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    id: UUID
    course_id: UUID
    outline_id: Optional[UUID]
    version: int
    name: str
    description: str
    style: str
    language_directive: str
    dsl_version: str
    provider_id: Optional[str]
    model_name: Optional[str]
    scene_count: int
    scenes: List[SceneView]
    created_at: datetime
    updated_at: datetime
