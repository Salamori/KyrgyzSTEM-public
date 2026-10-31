import uuid
from datetime import datetime
from typing import Any

from app.core.schemas import ResponseModel


class AuditLogEntry(ResponseModel):
    id: uuid.UUID
    actor_id: uuid.UUID | None
    action: str
    target_type: str | None
    target_id: str | None
    details: dict[str, Any]
    created_at: datetime
