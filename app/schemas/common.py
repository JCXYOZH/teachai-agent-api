from typing import Optional

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str
    environment: str


class ReadyResponse(BaseModel):
    status: str
    checks: dict
    detail: Optional[str] = None
