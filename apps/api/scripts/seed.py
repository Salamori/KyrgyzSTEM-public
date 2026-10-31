"""Seed clearly synthetic development data. Idempotent. Refuses to run in production.

Usage (from apps/api):  SEED_PASSWORD=... uv run python -m scripts.seed
"""

import sys
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_engine, utcnow
from app.core.security import hash_password
from app.modules.orgs.models import (
    AttendanceRecord,
    AttendanceStatus,
    Group,
    GroupKind,
    GroupMember,
    GroupSession,
    Organization,
    OrgKind,
    OrgMembership,
    OrgRole,
)
from app.modules.users.models import Locale, Role, User

SYNTHETIC_USERS = [
    ("student@example.com", "[Synthetic] Student Aidai", Role.student, Locale.ky),
    ("student2@example.com", "[Synthetic] Student Timur", Role.student, Locale.ru),
    ("teacher@example.com", "[Synthetic] Teacher Nurlan", Role.teacher, Locale.ru),
    ("admin@example.com", "[Synthetic] Admin", Role.admin, Locale.en),
]


def seed(db: Session, password: str) -> list[str]:
    created: list[str] = []
    for email, name, role, locale in SYNTHETIC_USERS:
        if db.scalar(select(User.id).where(User.email == email)) is not None:
            continue
        db.add(
            User(
                email=email,
                password_hash=hash_password(password),
                display_name=name,
                role=role,
                preferred_locale=locale,
                is_synthetic=True,
            )
        )
        created.append(email)
    db.commit()
    seed_demo_club(db)
    return created


DEMO_ORG = "[Synthetic] Demo Olympiad Club"


def seed_demo_club(db: Session) -> bool:
    """A synthetic club with one group, two members and three past sessions. Idempotent."""
    if db.scalar(select(Organization.id).where(Organization.name == DEMO_ORG)) is not None:
        return False
    users = {u.email: u for u in db.scalars(select(User).where(User.is_synthetic))}
    teacher = users.get("teacher@example.com")
    students = [users[e] for e in ("student@example.com", "student2@example.com") if e in users]
    if teacher is None:
        return False
    org = Organization(name=DEMO_ORG, kind=OrgKind.club, is_synthetic=True)
    db.add(org)
    db.flush()
    db.add(OrgMembership(org_id=org.id, user_id=teacher.id, role=OrgRole.org_admin))
    group = Group(
        org_id=org.id,
        name="[Synthetic] Weekly olympiad club",
        kind=GroupKind.club,
        lead_id=teacher.id,
    )
    db.add(group)
    db.flush()
    for s in students:
        db.add(OrgMembership(org_id=org.id, user_id=s.id, role=OrgRole.student))
        db.add(GroupMember(group_id=group.id, user_id=s.id))
    pattern = [AttendanceStatus.present, AttendanceStatus.absent, AttendanceStatus.present]
    for week in range(3):
        session = GroupSession(
            group_id=group.id,
            starts_at=utcnow() - timedelta(days=7 * (3 - week)),
            title=f"[Synthetic] Week {week + 1}",
            created_by=teacher.id,
        )
        db.add(session)
        db.flush()
        for i, s in enumerate(students):
            status = pattern[(week + i) % len(pattern)]
            db.add(
                AttendanceRecord(
                    session_id=session.id, user_id=s.id, status=status, marked_by=teacher.id
                )
            )
    db.commit()
    return True


def main() -> int:
    settings = get_settings()
    if settings.app_env == "production":
        print("Refusing to seed: APP_ENV=production.", file=sys.stderr)
        return 1
    password = settings.seed_password
    if len(password) < 10:
        print("Set SEED_PASSWORD (min 10 chars) in your environment or .env.", file=sys.stderr)
        return 1
    with Session(get_engine()) as db:
        created = seed(db, password)
    print(
        f"Seeded {len(created)} synthetic user(s): {', '.join(created) or 'none (already present)'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
