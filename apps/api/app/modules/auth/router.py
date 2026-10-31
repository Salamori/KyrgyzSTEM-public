from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_db, utcnow
from app.core.rate_limit import limiter
from app.core.security import tokens_match
from app.modules.auth import service
from app.modules.auth.deps import client_ip, get_current_session
from app.modules.auth.models import AuthSession
from app.modules.auth.schemas import (
    ChangePasswordRequest,
    LoginRequest,
    RegisterRequest,
    ResetPasswordRequest,
    SessionInfo,
)
from app.modules.users.schemas import UserPrivate

router = APIRouter(prefix="/auth", tags=["auth"])

_MINUTE = 60


def _set_cookies(response: Response, issued: service.IssuedSession) -> None:
    settings = get_settings()
    max_age = settings.session_ttl_hours * 3600
    response.set_cookie(
        settings.session_cookie_name,
        issued.token,
        max_age=max_age,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="lax",
        path="/",
    )
    _set_csrf_cookie(response, issued.csrf_token, max_age)


def _set_csrf_cookie(response: Response, csrf: str, max_age: int) -> None:
    settings = get_settings()
    # Readable by JS on purpose: the client echoes it in the X-CSRF-Token header.
    response.set_cookie(
        settings.csrf_cookie_name,
        csrf,
        max_age=max_age,
        httponly=False,
        secure=settings.session_cookie_secure,
        samesite="lax",
        path="/",
    )


def _clear_cookies(response: Response) -> None:
    settings = get_settings()
    for name in (settings.session_cookie_name, settings.csrf_cookie_name):
        response.delete_cookie(name, path="/", secure=settings.session_cookie_secure)


def _session_info(issued: service.IssuedSession) -> SessionInfo:
    return SessionInfo(
        user=UserPrivate.model_validate(issued.session.user),
        csrf_token=issued.csrf_token,
        expires_at=issued.session.expires_at,
    )


@router.post("/register", status_code=status.HTTP_201_CREATED)
def register(
    data: RegisterRequest, request: Request, response: Response, db: Session = Depends(get_db)
) -> SessionInfo:
    limiter.hit(f"register:{client_ip(request)}", limit=10, window_seconds=60 * _MINUTE)
    email = service.normalize_email(data.email)
    limiter.hit(f"register-email:{email}", limit=5, window_seconds=60 * _MINUTE)
    user = service.register(db, data)
    issued = service.issue_session(db, user, request.headers.get("user-agent"))
    db.commit()
    _set_cookies(response, issued)
    return _session_info(issued)


@router.post("/login")
def login(
    data: LoginRequest, request: Request, response: Response, db: Session = Depends(get_db)
) -> SessionInfo:
    ip = client_ip(request)
    email = service.normalize_email(data.email)
    limiter.hit(f"login-ip:{ip}", limit=50, window_seconds=15 * _MINUTE)
    limiter.hit(f"login:{ip}:{email}", limit=10, window_seconds=15 * _MINUTE)
    # Bounds guessing from many addresses (audit F-02). Unknown emails are counted the same.
    limiter.hit(f"login-account:{email}", limit=30, window_seconds=60 * _MINUTE)
    user = service.authenticate(db, data.email, data.password)
    issued = service.issue_session(db, user, request.headers.get("user-agent"))
    db.commit()
    _set_cookies(response, issued)
    return _session_info(issued)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    response: Response,
    session: AuthSession = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> None:
    service.revoke(session)
    db.commit()
    _clear_cookies(response)


@router.post("/logout-all", status_code=status.HTTP_204_NO_CONTENT)
def logout_all(
    response: Response,
    session: AuthSession = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> None:
    service.revoke_all(db, session.user)
    db.commit()
    _clear_cookies(response)


@router.get("/session")
def current_session(
    request: Request,
    response: Response,
    session: AuthSession = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> SessionInfo:
    # Keep a still-valid CSRF cookie (rotating it would race with in-flight requests);
    # issue a fresh one only if the browser lost it.
    csrf = request.cookies.get(get_settings().csrf_cookie_name, "")
    if not csrf or not tokens_match(csrf, session.csrf_token_hash):
        csrf = service.rotate_csrf(session)
        db.commit()
        remaining = int((session.expires_at - utcnow()).total_seconds())
        _set_csrf_cookie(response, csrf, max(remaining, 0))
    return SessionInfo(
        user=UserPrivate.model_validate(session.user),
        csrf_token=csrf,
        expires_at=session.expires_at,
    )


@router.post("/password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(
    data: ChangePasswordRequest,
    request: Request,
    response: Response,
    session: AuthSession = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> None:
    ip = client_ip(request)
    limiter.hit(f"password:{session.user_id}", limit=10, window_seconds=15 * _MINUTE)
    issued = service.change_password(
        db,
        session,
        data.current_password,
        data.new_password,
        ip,
        request.headers.get("user-agent"),
    )
    db.commit()
    _set_cookies(response, issued)


@router.post("/password/reset", status_code=status.HTTP_204_NO_CONTENT)
def reset_password(
    data: ResetPasswordRequest, request: Request, db: Session = Depends(get_db)
) -> None:
    ip = client_ip(request)
    limiter.hit(f"password-reset:{ip}", limit=10, window_seconds=15 * _MINUTE)
    service.reset_password(db, data.token, data.new_password, ip)
    db.commit()
