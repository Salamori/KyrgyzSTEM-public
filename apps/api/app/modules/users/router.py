import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_db
from app.core.errors import ApiError
from app.core.rate_limit import limiter
from app.core.schemas import Page
from app.modules.audit import service as audit
from app.modules.audit.models import AuditLog
from app.modules.audit.schemas import AuditLogEntry
from app.modules.auth.deps import client_ip, require_admin, require_user
from app.modules.auth.service import issue_password_reset, revoke_all
from app.modules.users import service
from app.modules.users.models import Role, User
from app.modules.users.schemas import (
    AccountExport,
    AdminUpdateUser,
    DeleteAccountRequest,
    PasswordResetIssued,
    UpdateMe,
    UserAdminView,
    UserPrivate,
)

router = APIRouter(tags=["users"])

Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0)]


@router.get("/users/me")
def get_me(user: User = Depends(require_user)) -> UserPrivate:
    return UserPrivate.model_validate(user)


@router.patch("/users/me")
def update_me(
    data: UpdateMe, user: User = Depends(require_user), db: Session = Depends(get_db)
) -> UserPrivate:
    if data.display_name is not None:
        user.display_name = data.display_name
    if data.preferred_locale is not None:
        user.preferred_locale = data.preferred_locale
    db.commit()
    return UserPrivate.model_validate(user)


@router.get("/users/me/export")
def export_me(user: User = Depends(require_user), db: Session = Depends(get_db)) -> AccountExport:
    limiter.hit(f"export:{user.id}", limit=5, window_seconds=3600)
    return service.export_account(db, user)


@router.post("/users/me/delete", status_code=status.HTTP_204_NO_CONTENT)
def delete_me(
    data: DeleteAccountRequest,
    request: Request,
    response: Response,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> None:
    limiter.hit(f"delete-account:{user.id}", limit=5, window_seconds=900)
    service.delete_account(db, user, data.password, client_ip(request))
    db.commit()
    settings = get_settings()
    for name in (settings.session_cookie_name, settings.csrf_cookie_name):
        response.delete_cookie(name, path="/", secure=settings.session_cookie_secure)


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@router.get("/admin/users")
def list_users(
    q: Annotated[str | None, Query(max_length=100)] = None,
    role: Role | None = None,
    limit: Limit = 20,
    offset: Offset = 0,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Page[UserAdminView]:
    stmt = select(User)
    if q and q.strip():
        pattern = f"%{_escape_like(q.strip().lower())}%"
        stmt = stmt.where(
            or_(
                func.lower(User.email).like(pattern, escape="\\"),
                func.lower(User.display_name).like(pattern, escape="\\"),
            )
        )
    if role is not None:
        stmt = stmt.where(User.role == role)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(User.created_at.desc(), User.id).limit(limit).offset(offset))
    return Page(
        items=[UserAdminView.model_validate(u) for u in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.patch("/admin/users/{user_id}")
def admin_update_user(
    user_id: uuid.UUID,
    data: AdminUpdateUser,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> UserAdminView:
    target = db.get(User, user_id)
    if target is None:
        raise ApiError(404, "not_found", "User not found.")
    if target.id == admin.id and (
        (data.role is not None and data.role != admin.role) or data.is_active is False
    ):
        raise ApiError(
            400, "cannot_modify_self", "You cannot change your own role or disable yourself."
        )

    ip = client_ip(request)
    if data.role is not None and data.role != target.role:
        audit.record(
            db,
            action="user.role_changed",
            actor_id=admin.id,
            target_type="user",
            target_id=target.id,
            details={"from": target.role.value, "to": data.role.value},
            ip=ip,
        )
        target.role = data.role
    if data.is_active is not None and data.is_active != target.is_active:
        audit.record(
            db,
            action="user.reactivated" if data.is_active else "user.deactivated",
            actor_id=admin.id,
            target_type="user",
            target_id=target.id,
            ip=ip,
        )
        target.is_active = data.is_active
        if not data.is_active:
            revoke_all(db, target)
    db.commit()
    return UserAdminView.model_validate(target)


@router.post("/admin/users/{user_id}/password-reset")
def admin_issue_password_reset(
    user_id: uuid.UUID,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> PasswordResetIssued:
    target = db.get(User, user_id)
    if target is None:
        raise ApiError(404, "not_found", "User not found.")
    if target.id == admin.id:
        raise ApiError(400, "cannot_modify_self", "Use the change password form instead.")
    if target.role == Role.admin:
        raise ApiError(
            403, "forbidden", "Admin accounts are recovered with scripts.issue_password_reset."
        )
    token, expires_at = issue_password_reset(db, target, admin.id, client_ip(request), "admin")
    db.commit()
    return PasswordResetIssued(token=token, expires_at=expires_at)


@router.get("/admin/audit-logs")
def list_audit_logs(
    action: Annotated[str | None, Query(max_length=64)] = None,
    limit: Limit = 20,
    offset: Offset = 0,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Page[AuditLogEntry]:
    stmt = select(AuditLog)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(AuditLog.created_at.desc(), AuditLog.id).limit(limit).offset(offset)
    )
    return Page(
        items=[AuditLogEntry.model_validate(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )
