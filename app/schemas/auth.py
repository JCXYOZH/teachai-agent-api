from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    field_validator,
    model_validator,
)

from app.core.security import mask_phone, normalize_phone


class RegisterRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    email: Optional[EmailStr] = None
    phone: Optional[str] = Field(default=None, max_length=32)
    password: str = Field(min_length=8, max_length=128)
    display_name: Optional[str] = Field(default=None, max_length=80)
    code: str = Field(min_length=6, max_length=6, pattern=r"^\d{6}$")

    @field_validator("password")
    @classmethod
    def validate_password_strength(cls, value: str) -> str:
        if not any(character.isalpha() for character in value):
            raise ValueError("Password must contain at least one letter")
        if not any(character.isdigit() for character in value):
            raise ValueError("Password must contain at least one digit")
        return value

    @field_validator("display_name")
    @classmethod
    def normalize_display_name(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, value: Optional[str]) -> Optional[str]:
        return _clean_phone(value)

    @model_validator(mode="after")
    def require_single_contact(self) -> "RegisterRequest":
        _require_single_contact(self.email, self.phone)
        return self


class LoginRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    identifier: str = Field(default="", max_length=320)
    email: Optional[str] = Field(default=None, max_length=320)
    password: str = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def resolve_identifier(self) -> "LoginRequest":
        value = self.identifier or self.email or ""
        if not value.strip():
            raise ValueError("An email or phone number is required")
        self.identifier = value
        return self


class SendRegistrationCodeRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    email: Optional[EmailStr] = None
    phone: Optional[str] = Field(default=None, max_length=32)

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, value: Optional[str]) -> Optional[str]:
        return _clean_phone(value)

    @model_validator(mode="after")
    def require_single_contact(self) -> "SendRegistrationCodeRequest":
        _require_single_contact(self.email, self.phone)
        return self


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: Optional[EmailStr] = None
    phone: Optional[str] = None
    display_name: str
    created_at: datetime

    @field_validator("phone")
    @classmethod
    def mask_phone_number(cls, value: Optional[str]) -> Optional[str]:
        if not value:
            return None
        return mask_phone(value)


class AuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserResponse


class RegistrationCodeResponse(BaseModel):
    channel: Literal["email", "phone"]
    target: str
    email: Optional[EmailStr] = None
    expires_in: int
    resend_after: int


def _clean_phone(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    try:
        return normalize_phone(normalized)
    except ValueError as exc:
        raise ValueError("Enter a valid mainland China mobile number") from exc


def _require_single_contact(email: Optional[str], phone: Optional[str]) -> None:
    if (email is None) == (phone is None):
        raise ValueError("Provide exactly one of email or phone")
