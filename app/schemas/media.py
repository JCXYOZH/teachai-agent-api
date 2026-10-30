from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class MediaAssetView(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    id: UUID
    kind: str
    format: str
    mime_type: str
    size_bytes: int
    duration_ms: Optional[int]
    provider_id: Optional[str]
    model_name: Optional[str]
    voice: Optional[str]
    course_id: UUID
    stage_id: UUID
    scene_id: Optional[UUID]
    action_id: Optional[str]
    content_url: str
    created_at: datetime
