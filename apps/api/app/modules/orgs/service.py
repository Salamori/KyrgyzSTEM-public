import csv
import io
import secrets
import uuid
from datetime import datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import utcnow
from app.core.errors import ApiError
from app.core.security import hash_token
from app.modules.audit import service as audit
from app.modules.orgs.models import (
    AttendanceRecord,
    AttendanceStatus,
    Group,
    GroupMember,
    GroupSession,
    Organization,
    OrgMembership,
    OrgRole,
)
from app.modules.orgs.permissions import OrgContext, Perm, group_in, managed_group, session_in
from app.modules.orgs.schemas import (
    AttendanceRow,
    AttendanceUpdate,
    GroupCreate,
    GroupMemberOut,
    GroupSummary,
    JoinCodeOut,
    JoinedOut,
    MemberOut,
    MyAttendanceRow,
    OrgAdminView,
    OrgCreate,
    OrgGroupOut,
    OrgOut,
    SessionCreate,
    SessionOut,
)
from app.modules.users.models import User

# No easily confused characters (0/O, 1/I/L).
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 8


def _user_by_email(db: Session, email: str) -> User:
    user = db.scalar(select(User).where(User.email == email.strip().lower()))
    if user is None or not user.is_active:
        raise ApiError(404, "not_found", "No active account with this email.")
    return user


def _set_role(db: Session, org_id: uuid.UUID, user_id: uuid.UUID, role: OrgRole) -> None:
    db.execute(
        pg_insert(OrgMembership)
        .values(id=uuid.uuid4(), org_id=org_id, user_id=user_id, role=role)
        .on_conflict_do_update(index_elements=["org_id", "user_id"], set_={"role": role})
    )


# ------------------------------------------------------------------ platform admin


def create_org(db: Session, actor: User, data: OrgCreate, ip: str) -> Organization:
    org = Organization(name=data.name, kind=data.kind, created_by=actor.id)
    db.add(org)
    db.flush()
    audit.record(
        db,
        action="org.created",
        actor_id=actor.id,
        target_type="organization",
        target_id=org.id,
        ip=ip,
    )
    return org


def admin_overview(db: Session) -> list[OrgAdminView]:
    counts = dict(
        ((org_id, role), n)
        for org_id, role, n in db.execute(
            select(OrgMembership.org_id, OrgMembership.role, func.count()).group_by(
                OrgMembership.org_id, OrgMembership.role
            )
        )
    )
    groups = dict(db.execute(select(Group.org_id, func.count()).group_by(Group.org_id)).all())
    return [
        OrgAdminView(
            id=o.id,
            name=o.name,
            kind=o.kind,
            is_synthetic=o.is_synthetic,
            admins=counts.get((o.id, OrgRole.org_admin), 0),
            teachers=counts.get((o.id, OrgRole.teacher), 0),
            students=counts.get((o.id, OrgRole.student), 0),
            groups=groups.get(o.id, 0),
        )
        for o in db.scalars(select(Organization).order_by(Organization.name))
    ]


def assign_org_admin(db: Session, actor: User, org_id: uuid.UUID, email: str, ip: str) -> None:
    if db.get(Organization, org_id) is None:
        raise ApiError(404, "not_found", "Organization not found.")
    user = _user_by_email(db, email)
    _set_role(db, org_id, user.id, OrgRole.org_admin)
    audit.record(
        db,
        action="org.admin_assigned",
        actor_id=actor.id,
        target_type="organization",
        target_id=org_id,
        details={"user_id": str(user.id)},
        ip=ip,
    )


# ------------------------------------------------------------------ organization


def my_orgs(db: Session, user: User) -> list[OrgOut]:
    rows = db.execute(
        select(Organization, OrgMembership.role)
        .join(OrgMembership, OrgMembership.org_id == Organization.id)
        .where(OrgMembership.user_id == user.id)
        .order_by(Organization.name)
    )
    return [
        OrgOut(id=o.id, name=o.name, kind=o.kind, is_synthetic=o.is_synthetic, my_role=role)
        for o, role in rows
    ]


def org_out(ctx: OrgContext) -> OrgOut:
    o = ctx.org
    return OrgOut(id=o.id, name=o.name, kind=o.kind, is_synthetic=o.is_synthetic, my_role=ctx.role)


def list_members(db: Session, ctx: OrgContext) -> list[MemberOut]:
    ctx.require(Perm.manage_staff)
    rows = db.execute(
        select(User.id, User.display_name, OrgMembership.role)
        .join(OrgMembership, OrgMembership.user_id == User.id)
        .where(OrgMembership.org_id == ctx.org.id)
        .order_by(OrgMembership.role, User.display_name)
    )
    return [MemberOut(user_id=i, display_name=n, role=r) for i, n, r in rows]


def _ensure_not_last_admin(db: Session, org_id: uuid.UUID) -> None:
    admins = db.scalar(
        select(func.count()).where(
            OrgMembership.org_id == org_id, OrgMembership.role == OrgRole.org_admin
        )
    )
    if admins == 1:
        raise ApiError(409, "last_org_admin", "An organization needs at least one admin.")


def add_staff(db: Session, ctx: OrgContext, email: str, role: OrgRole, ip: str) -> MemberOut:
    ctx.require(Perm.manage_staff)
    user = _user_by_email(db, email)
    current = db.scalar(
        select(OrgMembership.role).where(
            OrgMembership.org_id == ctx.org.id, OrgMembership.user_id == user.id
        )
    )
    if current == OrgRole.org_admin and role != OrgRole.org_admin:
        _ensure_not_last_admin(db, ctx.org.id)  # changing a role is a demotion here
    _set_role(db, ctx.org.id, user.id, role)
    audit.record(
        db,
        action="org.member_added",
        actor_id=ctx.user.id,
        target_type="organization",
        target_id=ctx.org.id,
        details={"user_id": str(user.id), "role": role.value},
        ip=ip,
    )
    return MemberOut(user_id=user.id, display_name=user.display_name, role=role)


def remove_member(db: Session, ctx: OrgContext, user_id: uuid.UUID, ip: str) -> None:
    ctx.require(Perm.manage_staff)
    membership = db.scalar(
        select(OrgMembership).where(
            OrgMembership.org_id == ctx.org.id, OrgMembership.user_id == user_id
        )
    )
    if membership is None:
        raise ApiError(404, "not_found", "Member not found.")
    if membership.role == OrgRole.org_admin:
        _ensure_not_last_admin(db, ctx.org.id)
    group_ids = select(Group.id).where(Group.org_id == ctx.org.id)
    _drop_from_groups(db, user_id, group_ids)
    db.delete(membership)
    audit.record(
        db,
        action="org.member_removed",
        actor_id=ctx.user.id,
        target_type="organization",
        target_id=ctx.org.id,
        details={"user_id": str(user_id)},
        ip=ip,
    )


def _drop_from_groups(db: Session, user_id: uuid.UUID, group_ids: object) -> None:
    """Leaving a group deletes that member's attendance there (retention policy)."""
    sessions = select(GroupSession.id).where(GroupSession.group_id.in_(group_ids))  # type: ignore[arg-type]
    db.execute(
        delete(AttendanceRecord).where(
            AttendanceRecord.user_id == user_id, AttendanceRecord.session_id.in_(sessions)
        )
    )
    db.execute(
        delete(GroupMember).where(
            GroupMember.user_id == user_id,
            GroupMember.group_id.in_(group_ids),  # type: ignore[arg-type]
        )
    )


# ------------------------------------------------------------------ groups


def _is_member(db: Session, group: Group, user: User) -> bool:
    return db.get(GroupMember, (group.id, user.id)) is not None


def visible_group(db: Session, ctx: OrgContext, group_id: uuid.UUID) -> Group:
    group = group_in(db, ctx, group_id)
    if not (ctx.can_manage(group) or _is_member(db, group, ctx.user)):
        raise ApiError(404, "not_found", "Group not found.")
    return group


def _group_out(db: Session, ctx: OrgContext, group: Group) -> OrgGroupOut:
    lead = db.get(User, group.lead_id) if group.lead_id else None
    manage = ctx.can_manage(group)
    return OrgGroupOut(
        id=group.id,
        org_id=group.org_id,
        name=group.name,
        kind=group.kind,
        lead_name=lead.display_name if lead else None,
        member_count=db.scalar(select(func.count()).where(GroupMember.group_id == group.id)) or 0,
        can_manage=manage,
        join_code_expires_at=group.join_code_expires_at if manage else None,
    )


def list_groups(db: Session, ctx: OrgContext) -> list[OrgGroupOut]:
    stmt = select(Group).where(Group.org_id == ctx.org.id)
    if not ctx.can(Perm.manage_all_groups):
        member_of = select(GroupMember.group_id).where(GroupMember.user_id == ctx.user.id)
        stmt = stmt.where((Group.lead_id == ctx.user.id) | Group.id.in_(member_of))
    return [_group_out(db, ctx, g) for g in db.scalars(stmt.order_by(Group.name))]


def group_detail(db: Session, ctx: OrgContext, group: Group) -> OrgGroupOut:
    return _group_out(db, ctx, group)


def create_group(db: Session, ctx: OrgContext, data: GroupCreate) -> OrgGroupOut:
    ctx.require(Perm.create_group)
    group = Group(org_id=ctx.org.id, name=data.name, kind=data.kind, lead_id=ctx.user.id)
    db.add(group)
    db.flush()
    return _group_out(db, ctx, group)


def rename_group(db: Session, ctx: OrgContext, group_id: uuid.UUID, name: str) -> OrgGroupOut:
    group = managed_group(db, ctx, group_id)
    group.name = name
    return _group_out(db, ctx, group)


def delete_group(db: Session, ctx: OrgContext, group_id: uuid.UUID, ip: str) -> None:
    group = managed_group(db, ctx, group_id)
    audit.record(
        db,
        action="group.deleted",
        actor_id=ctx.user.id,
        target_type="group",
        target_id=group.id,
        details={"org_id": str(ctx.org.id)},
        ip=ip,
    )
    db.delete(group)


def new_join_code(db: Session, ctx: OrgContext, group_id: uuid.UUID) -> JoinCodeOut:
    group = managed_group(db, ctx, group_id)
    code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
    group.join_code_hash = hash_token(code)
    group.join_code_expires_at = utcnow() + timedelta(days=get_settings().join_code_ttl_days)
    return JoinCodeOut(code=code, expires_at=group.join_code_expires_at)


def join(db: Session, user: User, code: str) -> JoinedOut:
    normalized = code.strip().upper().replace("-", "").replace(" ", "")
    group = db.scalar(
        select(Group).where(
            Group.join_code_hash == hash_token(normalized), Group.join_code_expires_at > utcnow()
        )
    )
    if group is None:
        raise ApiError(404, "invalid_join_code", "This code is wrong or has expired.")
    db.execute(
        pg_insert(OrgMembership)
        .values(id=uuid.uuid4(), org_id=group.org_id, user_id=user.id, role=OrgRole.student)
        .on_conflict_do_nothing(index_elements=["org_id", "user_id"])  # keep a staff role
    )
    db.execute(
        pg_insert(GroupMember).values(group_id=group.id, user_id=user.id).on_conflict_do_nothing()
    )
    return JoinedOut(org_id=group.org_id, group_id=group.id, group_name=group.name)


def group_members(db: Session, ctx: OrgContext, group_id: uuid.UUID) -> list[GroupMemberOut]:
    group = managed_group(db, ctx, group_id)
    rows = db.execute(
        select(User.id, User.display_name, GroupMember.joined_at)
        .join(GroupMember, GroupMember.user_id == User.id)
        .where(GroupMember.group_id == group.id)
        .order_by(User.display_name)
    )
    return [GroupMemberOut(user_id=i, display_name=n, joined_at=j) for i, n, j in rows]


def remove_group_member(
    db: Session, ctx: OrgContext, group_id: uuid.UUID, user_id: uuid.UUID, ip: str
) -> None:
    group = visible_group(db, ctx, group_id)
    leaving_self = user_id == ctx.user.id
    if not leaving_self and not ctx.can_manage(group):
        raise ApiError(403, "forbidden", "Only the group's teacher can remove members.")
    if db.get(GroupMember, (group.id, user_id)) is None:
        raise ApiError(404, "not_found", "Member not found.")
    _drop_from_groups(db, user_id, select(Group.id).where(Group.id == group.id))
    if not leaving_self:
        audit.record(
            db,
            action="group.member_removed",
            actor_id=ctx.user.id,
            target_type="group",
            target_id=group.id,
            details={"user_id": str(user_id)},
            ip=ip,
        )


# ------------------------------------------------------------------ sessions & attendance


def _counts(db: Session, session_ids: list[uuid.UUID]) -> dict[tuple[uuid.UUID, str], int]:
    if not session_ids:
        return {}
    rows = db.execute(
        select(AttendanceRecord.session_id, AttendanceRecord.status, func.count())
        .where(AttendanceRecord.session_id.in_(session_ids))
        .group_by(AttendanceRecord.session_id, AttendanceRecord.status)
    )
    return {(sid, status.value): n for sid, status, n in rows}


def list_sessions(db: Session, ctx: OrgContext, group_id: uuid.UUID) -> list[SessionOut]:
    group = visible_group(db, ctx, group_id)
    manage = ctx.can_manage(group)
    sessions = list(
        db.scalars(
            select(GroupSession)
            .where(GroupSession.group_id == group.id)
            .order_by(GroupSession.starts_at.desc())
        )
    )
    ids = [s.id for s in sessions]
    counts = _counts(db, ids) if manage else {}
    members = db.scalar(select(func.count()).where(GroupMember.group_id == group.id)) or 0
    mine = dict(
        db.execute(
            select(AttendanceRecord.session_id, AttendanceRecord.status).where(
                AttendanceRecord.user_id == ctx.user.id, AttendanceRecord.session_id.in_(ids)
            )
        ).all()
    )
    out = []
    for s in sessions:
        c = {k: counts.get((s.id, k), 0) for k in ("present", "absent", "excused")}
        out.append(
            SessionOut(
                id=s.id,
                starts_at=s.starts_at,
                title=s.title,
                present=c["present"] if manage else None,
                absent=c["absent"] if manage else None,
                excused=c["excused"] if manage else None,
                unmarked=max(0, members - sum(c.values())) if manage else None,
                my_status=mine.get(s.id),
            )
        )
    return out


def create_session(
    db: Session, ctx: OrgContext, group_id: uuid.UUID, data: SessionCreate
) -> uuid.UUID:
    group = managed_group(db, ctx, group_id)
    session = GroupSession(
        group_id=group.id,
        starts_at=data.starts_at,
        title=data.title or None,
        created_by=ctx.user.id,
    )
    db.add(session)
    db.flush()
    return session.id


def delete_session(
    db: Session, ctx: OrgContext, group_id: uuid.UUID, session_id: uuid.UUID
) -> None:
    group = managed_group(db, ctx, group_id)
    db.delete(session_in(db, group, session_id))


def attendance(
    db: Session, ctx: OrgContext, group_id: uuid.UUID, session_id: uuid.UUID
) -> list[AttendanceRow]:
    group = managed_group(db, ctx, group_id)
    session = session_in(db, group, session_id)
    status = dict(
        db.execute(
            select(AttendanceRecord.user_id, AttendanceRecord.status).where(
                AttendanceRecord.session_id == session.id
            )
        ).all()
    )
    return [
        AttendanceRow(user_id=m.user_id, display_name=m.display_name, status=status.get(m.user_id))
        for m in group_members(db, ctx, group.id)
    ]


def mark_attendance(
    db: Session, ctx: OrgContext, group_id: uuid.UUID, session_id: uuid.UUID, data: AttendanceUpdate
) -> list[AttendanceRow]:
    group = managed_group(db, ctx, group_id)
    session = session_in(db, group, session_id)
    members = set(db.scalars(select(GroupMember.user_id).where(GroupMember.group_id == group.id)))
    outsiders = [str(r.user_id) for r in data.records if r.user_id not in members]
    if outsiders:
        raise ApiError(
            422, "not_a_member", "Some students are not members of this group.", outsiders
        )
    now = utcnow()
    for r in data.records:
        db.execute(
            pg_insert(AttendanceRecord)
            .values(
                session_id=session.id,
                user_id=r.user_id,
                status=r.status,
                marked_by=ctx.user.id,
                marked_at=now,
            )
            .on_conflict_do_update(
                index_elements=["session_id", "user_id"],
                set_={"status": r.status, "marked_by": ctx.user.id, "marked_at": now},
            )
        )
    db.flush()
    return attendance(db, ctx, group.id, session.id)


def summary(db: Session, ctx: OrgContext, group_id: uuid.UUID) -> GroupSummary:
    group = managed_group(db, ctx, group_id)
    sessions = select(GroupSession.id).where(GroupSession.group_id == group.id)
    marked = db.scalar(select(func.count()).where(AttendanceRecord.session_id.in_(sessions))) or 0
    present = (
        db.scalar(
            select(func.count()).where(
                AttendanceRecord.session_id.in_(sessions),
                AttendanceRecord.status == AttendanceStatus.present,
            )
        )
        or 0
    )
    return GroupSummary(
        sessions=db.scalar(select(func.count()).select_from(sessions.subquery())) or 0,
        members=db.scalar(select(func.count()).where(GroupMember.group_id == group.id)) or 0,
        marked=marked,
        present=present,
        attendance_rate=(present / marked) if marked else None,
    )


def _cell(value: str) -> str:
    """Stop spreadsheet formula injection from display names or titles."""
    return "'" + value if value[:1] in ("=", "+", "-", "@", "\t", "\r") else value


def export_csv(db: Session, ctx: OrgContext, group_id: uuid.UUID, ip: str) -> tuple[str, str]:
    group = managed_group(db, ctx, group_id)
    members = group_members(db, ctx, group.id)
    sessions = list(
        db.scalars(
            select(GroupSession)
            .where(GroupSession.group_id == group.id)
            .order_by(GroupSession.starts_at)
        )
    )
    status = {
        (sid, uid): st.value
        for sid, uid, st in db.execute(
            select(
                AttendanceRecord.session_id, AttendanceRecord.user_id, AttendanceRecord.status
            ).where(AttendanceRecord.session_id.in_([s.id for s in sessions]))
        )
    }
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["session_start_utc", "session_title", "student", "status"])
    for s in sessions:
        for m in members:
            writer.writerow(
                [
                    s.starts_at.isoformat(),
                    _cell(s.title or ""),
                    _cell(m.display_name),
                    status.get((s.id, m.user_id), "unmarked"),
                ]
            )
    audit.record(
        db,
        action="report.exported",
        actor_id=ctx.user.id,
        target_type="group",
        target_id=group.id,
        details={"rows": len(sessions) * len(members)},
        ip=ip,
    )
    safe = "".join(ch if ch.isalnum() else "-" for ch in group.name)[:40] or "group"
    return buf.getvalue(), f"attendance-{safe}.csv"


def my_attendance(db: Session, user: User) -> list[MyAttendanceRow]:
    rows = db.execute(
        select(
            Organization.name,
            Group.name,
            GroupSession.starts_at,
            GroupSession.title,
            AttendanceRecord.status,
        )
        .join(GroupSession, GroupSession.id == AttendanceRecord.session_id)
        .join(Group, Group.id == GroupSession.group_id)
        .join(Organization, Organization.id == Group.org_id)
        .where(AttendanceRecord.user_id == user.id)
        .order_by(GroupSession.starts_at.desc())
        .limit(200)
    )
    return [
        MyAttendanceRow(org_name=o, group_name=g, starts_at=s, title=t, status=st)
        for o, g, s, t, st in rows
    ]


def purge_expired(db: Session, now: datetime) -> int:
    """Retention: delete sessions (and their attendance) older than the configured period."""
    cutoff = now - timedelta(days=get_settings().attendance_retention_days)
    result = db.execute(delete(GroupSession).where(GroupSession.starts_at < cutoff))
    return int(result.rowcount or 0)  # type: ignore[attr-defined]
