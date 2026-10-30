from datetime import datetime
from typing import Any, Dict, List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

SceneType = Literal["slide", "quiz", "interactive", "pbl"]
JobStatus = Literal["queued", "running", "completed", "failed"]
JobKind = Literal["outline", "classroom", "narration"]
ResponseKind = Literal["question", "microtask"]


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


class NarrationGenerateRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    provider_id: Optional[str] = Field(default=None, max_length=50)
    model: Optional[str] = Field(default=None, max_length=200)
    voice: Optional[str] = Field(default=None, max_length=120)

    @field_validator("model", "voice", "provider_id")
    @classmethod
    def normalize_optional_text(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        normalized = value.strip()
        if any(ord(character) < 32 for character in normalized):
            raise ValueError("Text contains unsupported control characters")
        return normalized or None


class GenerationJobView(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    id: UUID
    course_id: UUID
    kind: JobKind
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


class ClassroomAnswerItem(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    kind: ResponseKind
    target_id: str = Field(min_length=1, max_length=120)
    answer: Any

    @field_validator("answer")
    @classmethod
    def normalize_answer(cls, value: Any) -> Any:
        if isinstance(value, bool):
            return {"completed": value}
        if isinstance(value, str):
            text = value.strip()
            if not text:
                raise ValueError("Answer cannot be empty")
            if len(text) > 2000:
                raise ValueError("Answer is too long")
            return [text]
        if isinstance(value, dict):
            if set(value.keys()) != {"completed"} or not isinstance(
                value["completed"], bool
            ):
                raise ValueError(
                    'Microtask answers must be {"completed": true} or '
                    '{"completed": false}'
                )
            return {"completed": value["completed"]}
        if isinstance(value, list):
            if len(value) > 6:
                raise ValueError("Too many selected options")
            cleaned = [str(item).strip() for item in value]
            selected = [item for item in cleaned if item]
            if not selected:
                raise ValueError("Answer cannot be empty")
            if any(len(item) > 120 for item in selected):
                raise ValueError("Option value is too long")
            return selected
        raise ValueError("Unsupported answer payload")


class ClassroomResponseSubmitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    responses: List[ClassroomAnswerItem] = Field(min_length=1, max_length=30)


class ClassroomResultView(BaseModel):
    target_id: str
    kind: ResponseKind
    score: Optional[int]
    max_score: int
    correct: Optional[bool]
    reference_answer: List[str] = Field(default_factory=list)
    analysis: Optional[str] = None


class ClassroomSubmitView(BaseModel):
    scene_id: UUID
    stage_id: UUID
    results: List[ClassroomResultView]
    score: int
    max_score: int


class ClassroomResponseView(BaseModel):
    id: UUID
    course_id: UUID
    stage_id: UUID
    scene_id: UUID
    kind: ResponseKind
    target_id: str
    answer: Any
    score: Optional[int]
    max_score: int
    correct: Optional[bool]
    submitted_at: datetime


class ClassroomResponsesView(BaseModel):
    stage_id: Optional[UUID]
    responses: List[ClassroomResponseView]
