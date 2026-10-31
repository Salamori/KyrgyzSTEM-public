"""Import every model module so Base.metadata is complete (used by Alembic and tests)."""

from app.core.db import Base
from app.core.rate_limit import RateLimitHit
from app.modules.ai.models import AiRequest
from app.modules.audit.models import AuditLog
from app.modules.auth.models import AuthSession
from app.modules.olympiad import models as olympiad
from app.modules.orgs import models as orgs
from app.modules.users.models import User

__all__ = [
    "AiRequest",
    "AuditLog",
    "AuthSession",
    "Base",
    "RateLimitHit",
    "User",
    "olympiad",
    "orgs",
]
