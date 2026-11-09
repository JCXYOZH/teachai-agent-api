from datetime import datetime
from typing import List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.config import settings
from app.core.text import normalize_user_text_input

ChatRole = Literal["user", "assistant"]


class ChatMessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str
    stage_id: Optional[UUID] = None
    scene_id: Optional[UUID] = None
    include_materials: bool = False

    @field_validator("message", mode="before")
    @classmethod
    def clean_message(cls, value: object) -> object:
        return normalize_user_text_input(value, preserve_layout=True)

    @field_validator("message")
    @classmethod
    def validate_message(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Message cannot be empty")
        if len(value) > settings.chat_max_message_chars:
            raise ValueError(
                f"Message must be at most {settings.chat_max_message_chars} characters"
            )
        return value


class ChatMessageView(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    id: UUID
    role: ChatRole
    content: str
    stage_id: Optional[UUID]
    stage_title: Optional[str]
    stage_index: Optional[int]
    scene_id: Optional[UUID]
    scene_title: Optional[str]
    provider_id: Optional[str]
    model_name: Optional[str]
    created_at: datetime


class ChatThreadView(BaseModel):
    course_id: UUID
    session_id: Optional[UUID]
    messages: List[ChatMessageView] = Field(default_factory=list)
    daily_limit: int
    daily_used: int
    remaining_today: int


class ChatReplyView(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    course_id: UUID
    session_id: UUID
    question: ChatMessageView
    reply: ChatMessageView
    daily_limit: int
    daily_used: int
    remaining_today: int
