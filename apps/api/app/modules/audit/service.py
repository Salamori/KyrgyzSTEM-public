import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.modules.audit.models import AuditLog


def record(
    db: Session,
    *,
    action: str,
    actor_id: uuid.UUID | None,
    target_type: str | None = None,
    target_id: str | uuid.UUID | None = None,
    details: dict[str, Any] | None = None,
    ip: str | None = None,
) -> None:
    """Add an audit row to the current transaction (committed with the action itself)."""
    db.add(
        AuditLog(
            actor_id=actor_id,
            action=action,
            target_type=target_type,
            target_id=str(target_id) if target_id is not None else None,
            details=details or {},
            ip=ip,
        )
    )
