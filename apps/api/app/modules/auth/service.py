import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import utcnow
from app.core.errors import ApiError
from app.core.security import hash_password, hash_token, needs_rehash, new_token, verify_password
from app.modules.audit import service as audit
from app.modules.auth.models import AuthSession, PasswordResetToken
from app.modules.auth.schemas import RegisterRequest
from app.modules.users.models import Role, User


@dataclass(frozen=True)
class IssuedSession:
    session: AuthSession
    token: str
    csrf_token: str


def normalize_email(email: str) -> str:
    return email.strip().lower()


def issue_session(db: Session, user: User, user_agent: str | None) -> IssuedSession:
    token, csrf = new_token(), new_token()
    session = AuthSession(
        user=user,
        token_hash=hash_token(token),
        csrf_token_hash=hash_token(csrf),
        expires_at=utcnow() + timedelta(hours=get_settings().session_ttl_hours),
        user_agent=(user_agent or "")[:255] or None,
    )
    db.add(session)
    return IssuedSession(session=session, token=token, csrf_token=csrf)


def rotate_csrf(session: AuthSession) -> str:
    csrf = new_token()
    session.csrf_token_hash = hash_token(csrf)
    return csrf


def register(db: Session, data: RegisterRequest) -> User:
    # One insert, so concurrent duplicates cannot fail, and the password is hashed either way,
    # so timing does not reveal whether the email exists.
    # Role is never taken from the request: self-registration always yields a student.
    user_id = db.scalar(
        insert(User)
        .values(
            email=normalize_email(data.email),
            password_hash=hash_password(data.password),
            display_name=data.display_name,
            preferred_locale=data.preferred_locale,
            role=Role.student,
            terms_version=get_settings().legal_documents_version,
            terms_accepted_at=utcnow(),
        )
        .on_conflict_do_nothing(index_elements=[User.email])
        .returning(User.id)
    )
    if user_id is None:
        raise ApiError(409, "email_taken", "An account with this email already exists.")
    return db.get_one(User, user_id)


def authenticate(db: Session, email: str, password: str) -> User:
    user = db.scalar(select(User).where(User.email == normalize_email(email)))
    # verify_password runs a full Argon2 check even for unknown emails (timing parity).
    if not verify_password(user.password_hash if user else None, password) or user is None:
        raise ApiError(401, "invalid_credentials", "Email or password is incorrect.")
    if not user.is_active:
        raise ApiError(403, "account_disabled", "This account has been disabled.")
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    return user


def revoke(session: AuthSession) -> None:
    session.revoked_at = utcnow()


def revoke_all(db: Session, user: User, *, except_session: AuthSession | None = None) -> None:
    stmt = update(AuthSession).where(
        AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None)
    )
    if except_session is not None:
        stmt = stmt.where(AuthSession.id != except_session.id)
    db.execute(stmt.values(revoked_at=utcnow()))


def change_password(
    db: Session,
    session: AuthSession,
    current: str,
    new: str,
    ip: str | None,
    user_agent: str | None,
) -> IssuedSession:
    """Change the password; every session ends and the caller gets a new one."""
    user = session.user
    if not verify_password(user.password_hash, current):
        raise ApiError(401, "invalid_credentials", "Current password is incorrect.")
    user.password_hash = hash_password(new)
    revoke_all(db, user)
    audit.record(
        db,
        action="user.password_changed",
        actor_id=user.id,
        target_type="user",
        target_id=user.id,
        ip=ip,
    )
    return issue_session(db, user, user_agent)


def issue_password_reset(
    db: Session, user: User, actor_id: uuid.UUID | None, ip: str | None, via: str
) -> tuple[str, datetime]:
    """A new single-use token for `user`; earlier unused ones stop working. Returned once."""
    if not user.is_active:
        raise ApiError(409, "account_disabled", "This account has been disabled.")
    now = utcnow()
    db.execute(
        update(PasswordResetToken)
        .where(PasswordResetToken.user_id == user.id, PasswordResetToken.used_at.is_(None))
        .values(used_at=now)
    )
    token = new_token()
    expires_at = now + timedelta(minutes=get_settings().password_reset_ttl_minutes)
    db.add(
        PasswordResetToken(
            user_id=user.id,
            created_by=actor_id,
            token_hash=hash_token(token),
            expires_at=expires_at,
        )
    )
    audit.record(
        db,
        action="user.password_reset_issued",
        actor_id=actor_id,
        target_type="user",
        target_id=user.id,
        details={"via": via},
        ip=ip,
    )
    return token, expires_at


def reset_password(db: Session, token: str, new: str, ip: str | None) -> None:
    """Spend a reset token. Every failure looks the same, and all sessions end."""
    now = utcnow()
    row = db.scalar(
        select(PasswordResetToken)
        .where(
            PasswordResetToken.token_hash == hash_token(token),
            PasswordResetToken.used_at.is_(None),
            PasswordResetToken.expires_at > now,
        )
        .with_for_update()
    )
    user = db.get(User, row.user_id) if row is not None else None
    if row is None or user is None or not user.is_active:
        raise ApiError(400, "invalid_reset_token", "This link is invalid or has expired.")
    row.used_at = now
    user.password_hash = hash_password(new)
    revoke_all(db, user)
    audit.record(
        db,
        action="user.password_reset",
        actor_id=user.id,
        target_type="user",
        target_id=user.id,
        ip=ip,
    )
