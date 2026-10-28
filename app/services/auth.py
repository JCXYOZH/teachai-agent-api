import hmac
from dataclasses import dataclass
from datetime import timedelta
from typing import Optional

from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import AppError
from app.core.security import (
    create_access_token,
    generate_csrf_token,
    generate_refresh_token,
    hash_csrf_token,
    hash_password,
    hash_refresh_token,
    normalize_email,
    secure_compare,
    verify_password,
)
from app.core.time import utcnow
from app.models.auth_session import AuthSession
from app.models.user import User
from app.schemas.auth import LoginRequest, RegisterRequest

DUMMY_PASSWORD_HASH = hash_password("teachai-dummy-password-0")


@dataclass
class IssuedSession:
    user: User
    access_token: str
    refresh_token: str
    csrf_token: str
    expires_in: int


class AuthService:
    def __init__(self, db: AsyncSession, settings: Settings) -> None:
        self.db = db
        self.settings = settings

    async def register(
        self,
        payload: RegisterRequest,
        user_agent: Optional[str],
        ip_address: Optional[str],
    ) -> IssuedSession:
        email = normalize_email(str(payload.email))
        existing = await self.db.scalar(select(User).where(User.email == email))
        if existing is not None:
            raise AppError(
                409,
                "EMAIL_ALREADY_REGISTERED",
                "An account with this email already exists",
            )

        password_hash = await run_in_threadpool(hash_password, payload.password)
        display_name = payload.display_name or email.split("@", 1)[0][:80]
        user = User(
            email=email,
            password_hash=password_hash,
            display_name=display_name,
        )
        self.db.add(user)
        try:
            await self.db.flush()
        except IntegrityError as exc:
            await self.db.rollback()
            raise AppError(
                409,
                "EMAIL_ALREADY_REGISTERED",
                "An account with this email already exists",
            ) from exc

        issued = await self._issue_session(user, user_agent, ip_address)
        await self.db.commit()
        return issued

    async def login(
        self,
        payload: LoginRequest,
        user_agent: Optional[str],
        ip_address: Optional[str],
    ) -> IssuedSession:
        email = normalize_email(str(payload.email))
        user = await self.db.scalar(select(User).where(User.email == email))

        if user is None:
            await run_in_threadpool(verify_password, payload.password, DUMMY_PASSWORD_HASH)
            raise self._invalid_credentials()

        password_valid = await run_in_threadpool(
            verify_password, payload.password, user.password_hash
        )
        if not password_valid or not user.is_active:
            raise self._invalid_credentials()

        issued = await self._issue_session(user, user_agent, ip_address)
        await self.db.commit()
        return issued

    async def rotate_refresh_token(
        self,
        refresh_token: str,
        csrf_header: str,
        csrf_cookie: str,
        user_agent: Optional[str],
        ip_address: Optional[str],
    ) -> IssuedSession:
        token_hash = hash_refresh_token(refresh_token)
        auth_session = await self.db.scalar(
            select(AuthSession)
            .where(AuthSession.refresh_token_hash == token_hash)
            .with_for_update()
        )
        now = utcnow()

        if (
            auth_session is None
            or auth_session.revoked_at is not None
            or auth_session.expires_at <= now
        ):
            raise AppError(
                401,
                "INVALID_REFRESH_TOKEN",
                "Refresh token is invalid or expired",
            )

        if not hmac.compare_digest(csrf_header, csrf_cookie) or not secure_compare(
            csrf_header, auth_session.csrf_token_hash
        ):
            raise AppError(403, "CSRF_INVALID", "CSRF validation failed")

        user = await self.db.get(User, auth_session.user_id)
        if user is None or not user.is_active:
            raise self._invalid_credentials()

        auth_session.revoked_at = now
        auth_session.last_used_at = now
        issued = await self._issue_session(user, user_agent, ip_address)
        await self.db.commit()
        return issued

    async def logout(self, refresh_token: str, csrf_header: str, csrf_cookie: str) -> None:
        token_hash = hash_refresh_token(refresh_token)
        auth_session = await self.db.scalar(
            select(AuthSession)
            .where(AuthSession.refresh_token_hash == token_hash)
            .with_for_update()
        )
        if auth_session is None or auth_session.revoked_at is not None:
            return

        if not hmac.compare_digest(csrf_header, csrf_cookie) or not secure_compare(
            csrf_header, auth_session.csrf_token_hash
        ):
            raise AppError(403, "CSRF_INVALID", "CSRF validation failed")

        auth_session.revoked_at = utcnow()
        auth_session.last_used_at = utcnow()
        await self.db.commit()

    async def _issue_session(
        self,
        user: User,
        user_agent: Optional[str],
        ip_address: Optional[str],
    ) -> IssuedSession:
        refresh_token = generate_refresh_token()
        csrf_token = generate_csrf_token()
        expires_at = utcnow() + timedelta(days=self.settings.refresh_token_days)
        auth_session = AuthSession(
            user_id=user.id,
            refresh_token_hash=hash_refresh_token(refresh_token),
            csrf_token_hash=hash_csrf_token(csrf_token),
            expires_at=expires_at,
            user_agent=(user_agent or "")[:512] or None,
            ip_address=(ip_address or "")[:64] or None,
        )
        self.db.add(auth_session)
        await self.db.flush()
        return IssuedSession(
            user=user,
            access_token=create_access_token(user.id, self.settings),
            refresh_token=refresh_token,
            csrf_token=csrf_token,
            expires_in=self.settings.access_token_minutes * 60,
        )

    @staticmethod
    def _invalid_credentials() -> AppError:
        return AppError(401, "INVALID_CREDENTIALS", "Email or password is incorrect")
