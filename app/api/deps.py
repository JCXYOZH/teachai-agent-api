from typing import Optional

from fastapi import Depends, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import AppError
from app.core.security import TokenError, decode_access_token
from app.db.session import get_db
from app.models.user import User

bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Security(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise _authentication_required()

    try:
        user_id = decode_access_token(credentials.credentials, settings)
    except TokenError as exc:
        raise AppError(
            401,
            "INVALID_ACCESS_TOKEN",
            "Access token is invalid or expired",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    user = await db.get(User, user_id)
    if user is None or not user.is_active:
        raise _authentication_required()
    return user


def _authentication_required() -> AppError:
    return AppError(
        401,
        "UNAUTHORIZED",
        "Authentication required",
        headers={"WWW-Authenticate": "Bearer"},
    )
