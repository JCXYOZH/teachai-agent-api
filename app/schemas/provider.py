from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ProviderConfigUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    api_key: Optional[str] = Field(default=None, max_length=4096)
    clear_api_key: bool = False
    base_url: Optional[str] = Field(default=None, max_length=500)
    default_model: Optional[str] = Field(default=None, max_length=200)
    is_default: Optional[bool] = None

    @field_validator("api_key")
    @classmethod
    def normalize_api_key(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("API key must not be empty")
        if any(ord(character) < 32 for character in normalized):
            raise ValueError("API key contains unsupported control characters")
        return normalized

    @field_validator("base_url", "default_model")
    @classmethod
    def normalize_optional_text(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        normalized = value.strip()
        if any(ord(character) < 32 for character in normalized):
            raise ValueError("Text contains unsupported control characters")
        return normalized or None

    @model_validator(mode="after")
    def reject_conflicting_key_actions(self) -> "ProviderConfigUpdate":
        if self.api_key and self.clear_api_key:
            raise ValueError("api_key and clear_api_key cannot be used together")
        return self


class ProviderTestRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    api_key: Optional[str] = Field(default=None, max_length=4096)
    base_url: Optional[str] = Field(default=None, max_length=500)
    default_model: Optional[str] = Field(default=None, max_length=200)

    @field_validator("api_key")
    @classmethod
    def normalize_api_key(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("API key must not be empty")
        if any(ord(character) < 32 for character in normalized):
            raise ValueError("API key contains unsupported control characters")
        return normalized

    @field_validator("base_url", "default_model")
    @classmethod
    def normalize_optional_text(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        normalized = value.strip()
        if any(ord(character) < 32 for character in normalized):
            raise ValueError("Text contains unsupported control characters")
        return normalized or None


class ProviderView(BaseModel):
    id: str
    label: str
    description: str
    protocol: Literal["openai", "anthropic"]
    default_base_url: str
    alternate_base_urls: List[str]
    requires_api_key: bool
    supports_model_discovery: bool
    default_models: List[str]

    configured: bool
    api_key_configured: bool
    api_key_hint: Optional[str]
    api_key_source: Optional[Literal["user", "environment"]]
    base_url: str
    default_model: str
    is_default: bool
    updated_at: Optional[datetime]


class ProviderTestResponse(BaseModel):
    provider_id: str
    success: bool
    message: str
    latency_ms: int
    model: str
    models: List[str] = Field(default_factory=list)
