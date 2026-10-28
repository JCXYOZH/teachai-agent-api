import base64
import hashlib
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import Settings


class DecryptionError(Exception):
    pass


def _fernet(settings: Settings) -> Fernet:
    secret = settings.provider_key_encryption_secret or settings.jwt_secret
    digest = hashlib.sha256(f"teachai-provider-config:v1:{secret}".encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(value: str, settings: Settings) -> str:
    return _fernet(settings).encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_secret(value: Optional[str], settings: Settings) -> Optional[str]:
    if not value:
        return None
    try:
        return _fernet(settings).decrypt(value.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, TypeError) as exc:
        raise DecryptionError("Stored provider credential could not be decrypted") from exc
