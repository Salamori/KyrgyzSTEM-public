import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, UUIDPrimaryKey, utcnow


class AiRequest(UUIDPrimaryKey, Base):
    """One assistant request: metadata for quotas, cost and reproducibility.

    Deliberately stores no prompt, question, code or answer text.
    """

    __tablename__ = "ai_requests"
    __table_args__ = (
        Index("ix_ai_requests_user_created", "user_id", "created_at"),
        CheckConstraint("level BETWEEN 1 AND 4", name="level"),
        CheckConstraint("mode IN ('ai', 'fallback')", name="mode"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    problem_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("problems.id", ondelete="CASCADE"), index=True
    )
    level: Mapped[int] = mapped_column(Integer)
    locale_requested: Mapped[str] = mapped_column(String(2))
    locale_answered: Mapped[str] = mapped_column(String(2))
    mode: Mapped[str] = mapped_column(String(8))
    outcome: Mapped[str] = mapped_column(String(40))
    provider: Mapped[str | None] = mapped_column(String(32))
    model_id: Mapped[str | None] = mapped_column(String(80))
    prompt_version: Mapped[str] = mapped_column(String(32))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Numeric(10, 6), default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    flags: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
