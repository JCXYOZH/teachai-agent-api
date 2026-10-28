from functools import lru_cache
from pathlib import Path
from typing import List, Optional

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_JWT_SECRET = "development-only-secret-change-before-production-5f2a9c"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "TeachAI Agent API"
    app_version: str = "0.1.0"
    app_env: str = "development"
    api_v1_prefix: str = "/api/v1"

    database_url: str = (
        "postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/teachai_agent"
    )
    jwt_secret: str = DEFAULT_JWT_SECRET
    jwt_algorithm: str = "HS256"
    jwt_issuer: str = "teachai-agent-api"
    access_token_minutes: int = 15
    refresh_token_days: int = 30

    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    log_level: str = "INFO"
    trust_proxy_headers: bool = False
    cookie_secure: bool = False
    cookie_domain: Optional[str] = None
    refresh_cookie_name: str = "teachai_refresh"
    csrf_cookie_name: str = "teachai_csrf"

    asset_storage_root: Path = Path(r"E:\Backups\Data\TeachAIAgent")
    asset_max_bytes: int = 50 * 1024 * 1024
    asset_max_extracted_chars: int = 2_000_000
    asset_parse_timeout_seconds: float = 60.0

    llama_base_url: str = "http://127.0.0.1:65535/v1"
    llama_api_key: str = ""
    llama_model: str = "Qwen3.6-35B-A3B"

    provider_key_encryption_secret: Optional[str] = None
    provider_test_timeout_seconds: float = 20.0
    outline_material_char_limit: int = 16_000
    outline_generation_timeout_seconds: float = 600.0
    classroom_material_char_limit: int = 12_000
    classroom_generation_timeout_seconds: float = 600.0
    openai_api_key: str = ""
    anthropic_api_key: str = ""
    deepseek_api_key: str = ""
    qwen_api_key: str = ""
    dashscope_api_key: str = ""
    glm_api_key: str = ""
    kimi_api_key: str = ""

    @model_validator(mode="after")
    def validate_security_config(self) -> "Settings":
        if self.app_env == "production" and self.jwt_secret == DEFAULT_JWT_SECRET:
            raise ValueError("JWT_SECRET must be changed in production")
        if len(self.jwt_secret) < 32:
            raise ValueError("JWT_SECRET must contain at least 32 characters")
        if self.access_token_minutes < 1 or self.access_token_minutes > 1440:
            raise ValueError("ACCESS_TOKEN_MINUTES must be between 1 and 1440")
        if self.refresh_token_days < 1 or self.refresh_token_days > 365:
            raise ValueError("REFRESH_TOKEN_DAYS must be between 1 and 365")
        if not self.asset_storage_root.is_absolute():
            raise ValueError("ASSET_STORAGE_ROOT must be an absolute path")
        if self.asset_max_bytes < 1 or self.asset_max_bytes > 1024 * 1024 * 1024:
            raise ValueError("ASSET_MAX_BYTES must be between 1 and 1073741824")
        if (
            self.asset_max_extracted_chars < 1000
            or self.asset_max_extracted_chars > 20_000_000
        ):
            raise ValueError(
                "ASSET_MAX_EXTRACTED_CHARS must be between 1000 and 20000000"
            )
        if self.asset_parse_timeout_seconds < 1 or self.asset_parse_timeout_seconds > 600:
            raise ValueError("ASSET_PARSE_TIMEOUT_SECONDS must be between 1 and 600")
        if self.provider_test_timeout_seconds < 1 or self.provider_test_timeout_seconds > 120:
            raise ValueError("PROVIDER_TEST_TIMEOUT_SECONDS must be between 1 and 120")
        if (
            self.outline_material_char_limit < 1000
            or self.outline_material_char_limit > 200_000
        ):
            raise ValueError(
                "OUTLINE_MATERIAL_CHAR_LIMIT must be between 1000 and 200000"
            )
        if (
            self.outline_generation_timeout_seconds < 60
            or self.outline_generation_timeout_seconds > 1800
        ):
            raise ValueError(
                "OUTLINE_GENERATION_TIMEOUT_SECONDS must be between 60 and 1800"
            )
        if (
            self.classroom_material_char_limit < 1000
            or self.classroom_material_char_limit > 200_000
        ):
            raise ValueError(
                "CLASSROOM_MATERIAL_CHAR_LIMIT must be between 1000 and 200000"
            )
        if (
            self.classroom_generation_timeout_seconds < 60
            or self.classroom_generation_timeout_seconds > 1800
        ):
            raise ValueError(
                "CLASSROOM_GENERATION_TIMEOUT_SECONDS must be between 60 and 1800"
            )
        return self

    @property
    def cors_origin_list(self) -> List[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
