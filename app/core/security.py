import hashlib
import hmac
import secrets
from datetime import timedelta
from typing import Any, Dict
from uuid import UUID, uuid4

import jwt
from passlib.context import CryptContext

from app.core.config import Settings
from app.core.time import utcnow

password_context = CryptContext(
    schemes=["bcrypt_sha256"],
    deprecated="auto",
    bcrypt_sha256__rounds=12,
)


class TokenError(Exception):
    pass


def normalize_email(email: str) -> str:
    return email.strip().lower()


def hash_password(password: str) -> str:
    return password_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return password_context.verify(password, password_hash)
    except (ValueError, TypeError):
        return False


def create_access_token(user_id: UUID, settings: Settings) -> str:
    now = utcnow()
    expires_at = now + timedelta(minutes=settings.access_token_minutes)
    payload: Dict[str, Any] = {
        "sub": str(user_id),
        "type": "access",
        "jti": str(uuid4()),
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
        "iss": settings.jwt_issuer,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str, settings: Settings) -> UUID:
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            issuer=settings.jwt_issuer,
            options={"require": ["sub", "type", "iat", "exp", "jti"]},
        )
        if payload.get("type") != "access":
            raise TokenError("Unexpected token type")
        return UUID(payload["sub"])
    except (jwt.PyJWTError, ValueError, TypeError, KeyError) as exc:
        raise TokenError("Invalid or expired access token") from exc


def generate_refresh_token() -> str:
    return secrets.token_urlsafe(48)


def hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def hash_csrf_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def secure_compare(raw_value: str, expected_hash: str) -> bool:
    if not raw_value or not expected_hash:
        return False
    return hmac.compare_digest(hash_csrf_token(raw_value), expected_hash)
