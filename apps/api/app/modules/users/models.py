from datetime import datetime
from enum import StrEnum

from sqlalchemy import Boolean, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, Timestamps, UUIDPrimaryKey, str_enum


class Role(StrEnum):
    student = "student"
    teacher = "teacher"
    admin = "admin"


class Locale(StrEnum):
    ky = "ky"
    ru = "ru"
    en = "en"


class User(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "users"

    # Deliberately minimal personal data; see docs/SECURITY.md before adding fields.
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str] = mapped_column(String(80))
    role: Mapped[Role] = mapped_column(str_enum(Role, "role"), default=Role.student)
    preferred_locale: Mapped[Locale] = mapped_column(str_enum(Locale, "locale"), default=Locale.ru)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False)
    # Set at registration; NULL for accounts older than migration 0011.
    terms_version: Mapped[str | None] = mapped_column(String(32))
    terms_accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
