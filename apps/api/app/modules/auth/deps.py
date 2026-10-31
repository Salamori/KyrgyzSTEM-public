import ipaddress
import logging
from collections.abc import Callable
from datetime import timedelta

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_db, utcnow
from app.core.errors import ApiError
from app.core.security import hash_token, tokens_match
from app.modules.auth.models import AuthSession
from app.modules.users.models import Role, User

logger = logging.getLogger("kyrgyzstem.auth")

_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
_LAST_SEEN_GRANULARITY = timedelta(minutes=5)


def client_ip(request: Request) -> str:
    if get_settings().client_ip_source == "vercel":
        return _vercel_client_ip(request)
    # Uvicorn's proxy-headers middleware resolves X-Forwarded-For from trusted proxies.
    return request.client.host if request.client else "unknown"


def _vercel_client_ip(request: Request) -> str:
    """The client address Vercel's edge wrote into x-forwarded-for (audit F-01).

    Vercel overwrites this header with the client's public IP and never forwards a value the
    client sent, and on Vercel the API has no other ingress; config.py allows this mode only
    there (VERCEL=1). Anything but exactly one IP address means the request did not arrive
    as documented: refuse it rather than give every such request one shared identity.
    """
    values = request.headers.getlist("x-forwarded-for")
    try:
        if len(values) != 1:
            raise ValueError
        return str(ipaddress.ip_address(values[0].strip()))
    except ValueError:
        logger.warning("client_ip.unavailable")  # never the header itself: it is an IP
        raise ApiError(
            503, "service_unavailable", "The service is temporarily unavailable."
        ) from None


def _lookup_session(request: Request, db: Session) -> AuthSession | None:
    settings = get_settings()
    raw = request.cookies.get(settings.session_cookie_name)
    if not raw:
        return None
    now = utcnow()
    session = db.scalar(
        select(AuthSession).where(
            AuthSession.token_hash == hash_token(raw),
            AuthSession.revoked_at.is_(None),
            AuthSession.expires_at > now,
            AuthSession.last_seen_at > now - timedelta(hours=settings.session_idle_hours),
        )
    )
    return session if session is not None and session.user.is_active else None


def get_optional_user(request: Request, db: Session = Depends(get_db)) -> User | None:
    """For public read-only routes that personalise output when signed in."""
    if request.method not in _SAFE_METHODS:
        raise RuntimeError("get_optional_user is for safe methods only (no CSRF check)")
    session = _lookup_session(request, db)
    return session.user if session else None


def get_current_session(request: Request, db: Session = Depends(get_db)) -> AuthSession:
    """Resolve the session cookie. Unsafe methods additionally require a valid CSRF token."""
    settings = get_settings()
    if not request.cookies.get(settings.session_cookie_name):
        raise ApiError(401, "not_authenticated", "Sign in to continue.")
    session = _lookup_session(request, db)
    if session is None:
        raise ApiError(401, "not_authenticated", "Your session has ended. Sign in again.")
    now = utcnow()

    if request.method not in _SAFE_METHODS:
        header = request.headers.get(settings.csrf_header_name, "")
        if not header or not tokens_match(header, session.csrf_token_hash):
            raise ApiError(403, "csrf_failed", "Security check failed. Reload and try again.")

    if now - session.last_seen_at > _LAST_SEEN_GRANULARITY:
        session.last_seen_at = now
        db.commit()
    return session


def require_user(session: AuthSession = Depends(get_current_session)) -> User:
    return session.user


def require_role(*roles: Role) -> Callable[[User], User]:
    allowed = set(roles)

    def dependency(user: User = Depends(require_user)) -> User:
        if user.role not in allowed:
            raise ApiError(403, "forbidden", "You do not have permission to do this.")
        return user

    return dependency


require_admin = require_role(Role.admin)
