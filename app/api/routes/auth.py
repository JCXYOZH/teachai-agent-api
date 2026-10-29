import hmac
from typing import Optional

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.errors import AppError
from app.core.middleware import get_client_ip
from app.db.session import get_db
from app.models.user import User
from app.schemas.auth import (
    AuthResponse,
    LoginRequest,
    RegisterRequest,
    RegistrationCodeResponse,
    SendRegistrationCodeRequest,
    UserResponse,
)
from app.services.auth import AuthService, IssuedSession

router = APIRouter(prefix="/auth", tags=["auth"])
REFRESH_COOKIE_PATH = f"{settings.api_v1_prefix}/auth"


def _set_auth_cookies(response: Response, issued: IssuedSession) -> None:
    max_age = settings.refresh_token_days * 24 * 60 * 60
    response.set_cookie(
        key=settings.refresh_cookie_name,
        value=issued.refresh_token,
        max_age=max_age,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path=REFRESH_COOKIE_PATH,
        domain=settings.cookie_domain,
    )
    response.set_cookie(
        key=settings.csrf_cookie_name,
        value=issued.csrf_token,
        max_age=max_age,
        httponly=False,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
        domain=settings.cookie_domain,
    )


def _clear_auth_cookies(response: Response) -> None:
    response.delete_cookie(
        key=settings.refresh_cookie_name,
        path=REFRESH_COOKIE_PATH,
        domain=settings.cookie_domain,
        secure=settings.cookie_secure,
        httponly=True,
        samesite="lax",
    )
    response.delete_cookie(
        key=settings.csrf_cookie_name,
        path="/",
        domain=settings.cookie_domain,
        secure=settings.cookie_secure,
        httponly=False,
        samesite="lax",
    )


def _to_auth_response(issued: IssuedSession) -> AuthResponse:
    return AuthResponse(
        access_token=issued.access_token,
        expires_in=issued.expires_in,
        user=UserResponse.model_validate(issued.user),
    )


def _request_metadata(request: Request) -> tuple:
    return request.headers.get("user-agent"), get_client_ip(request, settings)


def _require_csrf(request: Request) -> tuple:
    csrf_header = request.headers.get("x-csrf-token")
    csrf_cookie = request.cookies.get(settings.csrf_cookie_name)
    if not csrf_header or not csrf_cookie:
        raise AppError(403, "CSRF_INVALID", "CSRF validation failed")
    if not hmac.compare_digest(csrf_header, csrf_cookie):
        raise AppError(403, "CSRF_INVALID", "CSRF validation failed")
    return csrf_header, csrf_cookie


@router.post(
    "/register/send-code",
    response_model=RegistrationCodeResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def send_registration_code(
    payload: SendRegistrationCodeRequest,
    db: AsyncSession = Depends(get_db),
) -> RegistrationCodeResponse:
    pending = await AuthService(db, settings).send_registration_code(payload)
    return RegistrationCodeResponse(
        email=pending.email,
        expires_in=pending.expires_in,
        resend_after=pending.resend_after,
    )


@router.post(
    "/register",
    response_model=AuthResponse,
    status_code=status.HTTP_201_CREATED,
)
async def register(
    payload: RegisterRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> AuthResponse:
    user_agent, ip_address = _request_metadata(request)
    issued = await AuthService(db, settings).register(payload, user_agent, ip_address)
    _set_auth_cookies(response, issued)
    return _to_auth_response(issued)


@router.post("/login", response_model=AuthResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> AuthResponse:
    user_agent, ip_address = _request_metadata(request)
    issued = await AuthService(db, settings).login(payload, user_agent, ip_address)
    _set_auth_cookies(response, issued)
    return _to_auth_response(issued)


@router.post("/refresh", response_model=AuthResponse)
async def refresh(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> AuthResponse:
    refresh_token: Optional[str] = request.cookies.get(settings.refresh_cookie_name)
    if not refresh_token:
        raise AppError(
            401,
            "INVALID_REFRESH_TOKEN",
            "Refresh token is invalid or expired",
        )
    csrf_header, csrf_cookie = _require_csrf(request)
    user_agent, ip_address = _request_metadata(request)
    issued = await AuthService(db, settings).rotate_refresh_token(
        refresh_token=refresh_token,
        csrf_header=csrf_header,
        csrf_cookie=csrf_cookie,
        user_agent=user_agent,
        ip_address=ip_address,
    )
    _set_auth_cookies(response, issued)
    return _to_auth_response(issued)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> None:
    refresh_token: Optional[str] = request.cookies.get(settings.refresh_cookie_name)
    if refresh_token:
        csrf_header, csrf_cookie = _require_csrf(request)
        await AuthService(db, settings).logout(
            refresh_token=refresh_token,
            csrf_header=csrf_header,
            csrf_cookie=csrf_cookie,
        )
    _clear_auth_cookies(response)


@router.get("/me", response_model=UserResponse)
async def me(current_user: User = Depends(get_current_user)) -> UserResponse:
    return UserResponse.model_validate(current_user)
