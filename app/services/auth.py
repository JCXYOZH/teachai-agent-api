import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256
from typing import Optional, Tuple, Union

from fastapi.concurrency import run_in_threadpool
from sqlalchemy import delete, or_, select
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
    mask_phone,
    normalize_email,
    normalize_phone,
    normalize_phone_or_none,
    secure_compare,
    verify_password,
)
from app.core.time import utcnow
from app.db.session import AsyncSessionLocal
from app.models.auth_session import AuthSession
from app.models.email_verification import EmailVerification
from app.models.phone_verification import PhoneVerification
from app.models.user import User
from app.models.verification_log import VerificationSendLog
from app.schemas.auth import (
    LoginRequest,
    RegisterRequest,
    SendRegistrationCodeRequest,
)
from app.services.email import EmailDeliveryError, get_email_sender
from app.services.sms import SmsDeliveryError, get_sms_sender

DUMMY_PASSWORD_HASH = hash_password("teachai-dummy-password-0")


@dataclass
class IssuedSession:
    user: User
    access_token: str
    refresh_token: str
    csrf_token: str
    expires_in: int


@dataclass
class PendingRegistration:
    channel: str
    target: str
    email: Optional[str]
    expires_in: int
    resend_after: int


def generate_verification_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def hash_verification_code(secret: str, email: str, code: str) -> str:
    message = f"{email}:{code}".encode()
    return hmac.new(secret.encode("utf-8"), message, sha256).hexdigest()


class AuthService:
    def __init__(self, db: AsyncSession, settings: Settings) -> None:
        self.db = db
        self.settings = settings

    async def send_registration_code(
        self,
        payload: SendRegistrationCodeRequest,
        ip_address: Optional[str] = None,
    ) -> PendingRegistration:
        if payload.phone is not None:
            return await self._send_phone_code(payload.phone, ip_address)
        return await self._send_email_code(normalize_email(str(payload.email)))

    async def _send_email_code(self, email: str) -> PendingRegistration:
        await self._ensure_email_available(email)
        pending, code = await self._upsert_pending(email)
        await self._deliver_code(pending, code)
        return self._pending_response(pending)

    async def _send_phone_code(
        self, phone: str, ip_address: Optional[str]
    ) -> PendingRegistration:
        await self._enforce_sms_send_limits(phone, ip_address)
        await self._ensure_phone_available(phone)
        pending, code = await self._upsert_phone_pending(phone)
        await self._deliver_sms_code(pending, code)
        return self._phone_pending_response(pending)

    async def register(
        self,
        payload: RegisterRequest,
        user_agent: Optional[str],
        ip_address: Optional[str],
    ) -> IssuedSession:
        if payload.phone is not None:
            return await self._register_with_phone(payload, user_agent, ip_address)
        return await self._register_with_email(payload, user_agent, ip_address)

    async def _register_with_email(
        self,
        payload: RegisterRequest,
        user_agent: Optional[str],
        ip_address: Optional[str],
    ) -> IssuedSession:
        email = normalize_email(str(payload.email))
        pending = await self._pending_for_update(email)
        now = utcnow()
        if pending is None:
            await self._raise_already_registered_or_missing(email)
        assert pending is not None

        self._ensure_pending_usable(pending, now)
        await self._verify_pending_code(pending, email, str(payload.code))

        existing = await self.db.scalar(select(User).where(User.email == email))
        if existing is not None:
            pending.consumed_at = now
            await self.db.commit()
            raise AppError(
                409,
                "EMAIL_ALREADY_REGISTERED",
                "An account with this email already exists",
            )

        password_hash = await run_in_threadpool(hash_password, payload.password)
        display_name = payload.display_name or email.split("@", 1)[0][:80]
        user = User(email=email, password_hash=password_hash, display_name=display_name)
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

        pending.consumed_at = now
        issued = await self._issue_session(user, user_agent, ip_address)
        await self.db.commit()
        return issued

    async def _register_with_phone(
        self,
        payload: RegisterRequest,
        user_agent: Optional[str],
        ip_address: Optional[str],
    ) -> IssuedSession:
        phone = normalize_phone(str(payload.phone))
        pending = await self._pending_phone_for_update(phone)
        now = utcnow()
        if pending is None:
            await self._raise_phone_registered_or_missing(phone)
        assert pending is not None

        self._ensure_pending_usable(pending, now)
        await self._verify_pending_code(pending, phone, str(payload.code))

        existing = await self.db.scalar(select(User).where(User.phone == phone))
        if existing is not None:
            pending.consumed_at = now
            await self.db.commit()
            raise AppError(
                409,
                "PHONE_ALREADY_REGISTERED",
                "An account with this phone number already exists",
            )

        password_hash = await run_in_threadpool(hash_password, payload.password)
        display_name = payload.display_name or f"用户{phone[-4:]}"
        user = User(phone=phone, password_hash=password_hash, display_name=display_name)
        self.db.add(user)
        try:
            await self.db.flush()
        except IntegrityError as exc:
            await self.db.rollback()
            raise AppError(
                409,
                "PHONE_ALREADY_REGISTERED",
                "An account with this phone number already exists",
            ) from exc

        pending.consumed_at = now
        issued = await self._issue_session(user, user_agent, ip_address)
        await self.db.commit()
        return issued

    def _ensure_pending_usable(
        self, pending: Union[EmailVerification, PhoneVerification], now: datetime
    ) -> None:
        if pending.expires_at <= now:
            raise AppError(
                400,
                "VERIFICATION_CODE_EXPIRED",
                "The verification code has expired",
            )
        if pending.attempts >= self.settings.verification_max_attempts:
            raise AppError(
                429,
                "VERIFICATION_ATTEMPTS_EXCEEDED",
                "Too many invalid verification attempts",
            )

    async def _verify_pending_code(
        self,
        pending: Union[EmailVerification, PhoneVerification],
        identifier: str,
        code: str,
    ) -> None:
        expected = hash_verification_code(self.settings.jwt_secret, identifier, code)
        if hmac.compare_digest(expected, pending.code_hash):
            return
        pending.attempts += 1
        await self.db.commit()
        remaining = max(0, self.settings.verification_max_attempts - pending.attempts)
        raise AppError(
            400,
            "INVALID_VERIFICATION_CODE",
            "The verification code is incorrect",
            details={"attempts_remaining": remaining},
        )

    async def _enforce_sms_send_limits(
        self, phone: str, ip_address: Optional[str]
    ) -> None:
        now = utcnow()
        rules = [
            (
                VerificationSendLog.target == phone,
                now - timedelta(hours=1),
                self.settings.sms_target_limit_per_hour,
                3600,
            )
        ]
        if ip_address:
            rules.append(
                (
                    VerificationSendLog.ip_address == ip_address,
                    now - timedelta(minutes=1),
                    self.settings.sms_ip_limit_per_minute,
                    60,
                )
            )
            rules.append(
                (
                    VerificationSendLog.ip_address == ip_address,
                    now - timedelta(hours=1),
                    self.settings.sms_ip_limit_per_hour,
                    3600,
                )
            )

        for condition, since, limit, window_seconds in rules:
            stamps = list(
                (
                    await self.db.scalars(
                        select(VerificationSendLog.created_at)
                        .where(condition, VerificationSendLog.created_at >= since)
                        .order_by(VerificationSendLog.created_at)
                    )
                ).all()
            )
            if len(stamps) >= limit:
                retry_after = max(
                    1,
                    int(
                        (
                            stamps[0]
                            + timedelta(seconds=window_seconds)
                            - now
                        ).total_seconds()
                    )
                    + 1,
                )
                raise AppError(
                    429,
                    "SMS_SEND_RATE_LIMITED",
                    "Too many verification SMS requests",
                    details={"retry_after": retry_after},
                )

        self.db.add(
            VerificationSendLog(
                channel="phone",
                target=phone,
                ip_address=(ip_address or "")[:64] or None,
                created_at=now,
            )
        )
        await self.db.commit()

    async def _ensure_email_available(self, email: str) -> None:
        existing = await self.db.scalar(select(User).where(User.email == email))
        if existing is not None:
            raise AppError(
                409,
                "EMAIL_ALREADY_REGISTERED",
                "An account with this email already exists",
            )

    async def _ensure_phone_available(self, phone: str) -> None:
        existing = await self.db.scalar(select(User).where(User.phone == phone))
        if existing is not None:
            raise AppError(
                409,
                "PHONE_ALREADY_REGISTERED",
                "An account with this phone number already exists",
            )

    async def _raise_already_registered_or_missing(self, email: str) -> None:
        existing = await self.db.scalar(select(User).where(User.email == email))
        if existing is not None:
            raise AppError(
                409,
                "EMAIL_ALREADY_REGISTERED",
                "An account with this email already exists",
            )
        raise AppError(
            400,
            "EMAIL_VERIFICATION_NOT_FOUND",
            "The verification code is missing or no longer valid",
        )

    async def _raise_phone_registered_or_missing(self, phone: str) -> None:
        existing = await self.db.scalar(select(User).where(User.phone == phone))
        if existing is not None:
            raise AppError(
                409,
                "PHONE_ALREADY_REGISTERED",
                "An account with this phone number already exists",
            )
        raise AppError(
            400,
            "PHONE_VERIFICATION_NOT_FOUND",
            "The verification code is missing or no longer valid",
        )

    async def _pending_for_update(self, email: str) -> Optional[EmailVerification]:
        return await self.db.scalar(
            select(EmailVerification)
            .where(
                EmailVerification.email == email,
                EmailVerification.consumed_at.is_(None),
            )
            .order_by(EmailVerification.created_at.desc())
            .limit(1)
            .with_for_update()
        )

    async def _pending_phone_for_update(
        self, phone: str
    ) -> Optional[PhoneVerification]:
        return await self.db.scalar(
            select(PhoneVerification)
            .where(
                PhoneVerification.phone == phone,
                PhoneVerification.consumed_at.is_(None),
            )
            .order_by(PhoneVerification.created_at.desc())
            .limit(1)
            .with_for_update()
        )

    async def _upsert_pending(self, email: str) -> Tuple[EmailVerification, str]:
        pending = await self._pending_for_update(email)
        if pending is not None:
            self._enforce_resend_cooldown(pending)
        else:
            pending = EmailVerification(
                email=email,
                code_hash="",
                expires_at=utcnow(),
                last_sent_at=utcnow(),
            )
            self.db.add(pending)
        code = generate_verification_code()
        self._apply_code(pending, code, pending.email)
        try:
            await self.db.commit()
        except IntegrityError as exc:
            await self.db.rollback()
            raise AppError(
                409,
                "REGISTRATION_ALREADY_PENDING",
                "A verification code was just sent for this email",
            ) from exc
        return pending, code

    async def _upsert_phone_pending(
        self, phone: str
    ) -> Tuple[PhoneVerification, str]:
        pending = await self._pending_phone_for_update(phone)
        if pending is not None:
            self._enforce_resend_cooldown(pending)
        else:
            pending = PhoneVerification(
                phone=phone,
                code_hash="",
                expires_at=utcnow(),
                last_sent_at=utcnow(),
            )
            self.db.add(pending)
        code = generate_verification_code()
        self._apply_code(pending, code, phone)
        try:
            await self.db.commit()
        except IntegrityError as exc:
            await self.db.rollback()
            raise AppError(
                409,
                "REGISTRATION_ALREADY_PENDING",
                "A verification code was just sent for this phone number",
            ) from exc
        return pending, code

    def _apply_code(
        self,
        pending: Union[EmailVerification, PhoneVerification],
        code: str,
        identifier: str,
    ) -> None:
        now = utcnow()
        pending.code_hash = hash_verification_code(
            self.settings.jwt_secret, identifier, code
        )
        pending.expires_at = now + timedelta(
            seconds=self.settings.verification_code_ttl_seconds
        )
        pending.last_sent_at = now
        pending.attempts = 0

    def _enforce_resend_cooldown(
        self, pending: Union[EmailVerification, PhoneVerification]
    ) -> None:
        next_allowed = pending.last_sent_at + timedelta(
            seconds=self.settings.verification_resend_seconds
        )
        now = utcnow()
        if next_allowed > now:
            retry_after = max(1, int((next_allowed - now).total_seconds()))
            raise AppError(
                429,
                "VERIFICATION_RESEND_TOO_SOON",
                "Please wait before requesting another code",
                details={"retry_after": retry_after},
            )

    async def _deliver_code(self, pending: EmailVerification, code: str) -> None:
        try:
            await get_email_sender(self.settings).send_verification_code(
                to=pending.email,
                code=code,
                ttl_seconds=self.settings.verification_code_ttl_seconds,
            )
        except EmailDeliveryError as exc:
            pending.expires_at = utcnow()
            pending.last_sent_at = utcnow() - timedelta(
                seconds=self.settings.verification_resend_seconds
            )
            await self.db.commit()
            raise AppError(
                503,
                "EMAIL_DELIVERY_UNAVAILABLE",
                "Verification email could not be sent, please try again later",
            ) from exc

    async def _deliver_sms_code(
        self, pending: PhoneVerification, code: str
    ) -> None:
        try:
            await get_sms_sender(self.settings).send_verification_code(
                to=pending.phone,
                code=code,
                ttl_seconds=self.settings.verification_code_ttl_seconds,
            )
        except SmsDeliveryError as exc:
            pending.expires_at = utcnow()
            pending.last_sent_at = utcnow() - timedelta(
                seconds=self.settings.verification_resend_seconds
            )
            await self.db.commit()
            raise AppError(
                503,
                "SMS_DELIVERY_UNAVAILABLE",
                "Verification SMS could not be sent, please try again later",
            ) from exc

    def _pending_response(self, pending: EmailVerification) -> PendingRegistration:
        return PendingRegistration(
            channel="email",
            target=pending.email,
            email=pending.email,
            expires_in=self.settings.verification_code_ttl_seconds,
            resend_after=self.settings.verification_resend_seconds,
        )

    def _phone_pending_response(
        self, pending: PhoneVerification
    ) -> PendingRegistration:
        return PendingRegistration(
            channel="phone",
            target=mask_phone(pending.phone),
            email=None,
            expires_in=self.settings.verification_code_ttl_seconds,
            resend_after=self.settings.verification_resend_seconds,
        )

    async def login(
        self,
        payload: LoginRequest,
        user_agent: Optional[str],
        ip_address: Optional[str],
    ) -> IssuedSession:
        identifier = payload.identifier
        phone = normalize_phone_or_none(identifier)
        if phone is not None:
            user = await self.db.scalar(select(User).where(User.phone == phone))
        else:
            user = await self.db.scalar(
                select(User).where(User.email == normalize_email(identifier))
            )

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
        return AppError(
            401,
            "INVALID_CREDENTIALS",
            "Email, phone number or password is incorrect",
        )


async def purge_stale_verifications() -> int:
    cutoff = utcnow() - timedelta(days=1)
    async with AsyncSessionLocal() as db:
        email_result = await db.execute(
            delete(EmailVerification).where(
                or_(
                    EmailVerification.consumed_at.is_not(None),
                    EmailVerification.expires_at < cutoff,
                )
            )
        )
        phone_result = await db.execute(
            delete(PhoneVerification).where(
                or_(
                    PhoneVerification.consumed_at.is_not(None),
                    PhoneVerification.expires_at < cutoff,
                )
            )
        )
        log_result = await db.execute(
            delete(VerificationSendLog).where(VerificationSendLog.created_at < cutoff)
        )
        await db.commit()
        return (
            int(getattr(email_result, "rowcount", 0) or 0)
            + int(getattr(phone_result, "rowcount", 0) or 0)
            + int(getattr(log_result, "rowcount", 0) or 0)
        )
