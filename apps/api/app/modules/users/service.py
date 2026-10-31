from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.db import utcnow
from app.core.errors import ApiError
from app.core.security import verify_password
from app.modules.ai.models import AiRequest
from app.modules.audit import service as audit
from app.modules.auth.models import AuthSession
from app.modules.olympiad.models import (
    EditorialUnlock,
    HintReveal,
    Problem,
    SelfReport,
    Submission,
)
from app.modules.orgs.models import (
    AttendanceRecord,
    Group,
    GroupMember,
    GroupSession,
    Organization,
    OrgMembership,
    OrgRole,
)
from app.modules.users.models import Role, User
from app.modules.users.schemas import (
    AccountExport,
    ExportAiRequest,
    ExportAttendance,
    ExportEditorialUnlock,
    ExportGroup,
    ExportHintReveal,
    ExportMembership,
    ExportSelfReport,
    ExportSession,
    ExportSubmission,
    UserPrivate,
)


def export_account(db: Session, user: User) -> AccountExport:
    """Selects named columns only, so a column added later never leaks into an export."""
    submissions = db.execute(
        select(
            Submission.id,
            Problem.slug,
            Submission.language,
            Submission.source_code,
            Submission.state,
            Submission.verdict,
            Submission.score,
            Submission.created_at,
        )
        .join(Problem, Problem.id == Submission.problem_id)
        .where(Submission.user_id == user.id)
        .order_by(Submission.created_at)
    )
    hints = db.execute(
        select(Problem.slug, HintReveal.ordinal, HintReveal.revealed_at)
        .join(Problem, Problem.id == HintReveal.problem_id)
        .where(HintReveal.user_id == user.id)
        .order_by(HintReveal.revealed_at)
    )
    unlocks = db.execute(
        select(Problem.slug, EditorialUnlock.reason, EditorialUnlock.unlocked_at)
        .join(Problem, Problem.id == EditorialUnlock.problem_id)
        .where(EditorialUnlock.user_id == user.id)
        .order_by(EditorialUnlock.unlocked_at)
    )
    reports = db.execute(
        select(Problem.slug, SelfReport.claim, SelfReport.created_at)
        .join(Problem, Problem.id == SelfReport.problem_id)
        .where(SelfReport.user_id == user.id)
        .order_by(SelfReport.created_at)
    )
    ai_requests = db.execute(
        select(Problem.slug, AiRequest.level, AiRequest.outcome, AiRequest.created_at)
        .join(Problem, Problem.id == AiRequest.problem_id)
        .where(AiRequest.user_id == user.id)
        .order_by(AiRequest.created_at)
    )
    sessions = db.execute(
        select(
            AuthSession.created_at,
            AuthSession.last_seen_at,
            AuthSession.expires_at,
            AuthSession.revoked_at,
            AuthSession.user_agent,
        )
        .where(AuthSession.user_id == user.id)
        .order_by(AuthSession.created_at)
    )
    memberships = db.execute(
        select(Organization.name, OrgMembership.role, OrgMembership.created_at)
        .join(Organization, Organization.id == OrgMembership.org_id)
        .where(OrgMembership.user_id == user.id)
        .order_by(OrgMembership.created_at)
    )
    groups = db.execute(
        select(Organization.name, Group.name, GroupMember.joined_at)
        .join(Group, Group.id == GroupMember.group_id)
        .join(Organization, Organization.id == Group.org_id)
        .where(GroupMember.user_id == user.id)
        .order_by(GroupMember.joined_at)
    )
    attendance = db.execute(
        select(Group.name, GroupSession.starts_at, GroupSession.title, AttendanceRecord.status)
        .join(GroupSession, GroupSession.id == AttendanceRecord.session_id)
        .join(Group, Group.id == GroupSession.group_id)
        .where(AttendanceRecord.user_id == user.id)
        .order_by(GroupSession.starts_at)
    )
    return AccountExport(
        exported_at=utcnow(),
        profile=UserPrivate.model_validate(user),
        terms_version=user.terms_version,
        terms_accepted_at=user.terms_accepted_at,
        sessions=[
            ExportSession(
                created_at=created,
                last_seen_at=seen,
                expires_at=expires,
                revoked_at=revoked,
                user_agent=agent,
            )
            for created, seen, expires, revoked, agent in sessions
        ],
        submissions=[
            ExportSubmission(
                id=row.id,
                problem_slug=row.slug,
                language=row.language.value,
                source_code=row.source_code,
                state=row.state.value,
                verdict=row.verdict.value if row.verdict else None,
                score=row.score,
                created_at=row.created_at,
            )
            for row in submissions
        ],
        hint_reveals=[
            ExportHintReveal(problem_slug=slug, ordinal=ordinal, revealed_at=at)
            for slug, ordinal, at in hints
        ],
        editorial_unlocks=[
            ExportEditorialUnlock(problem_slug=slug, reason=reason.value, unlocked_at=at)
            for slug, reason, at in unlocks
        ],
        self_reports=[
            ExportSelfReport(problem_slug=slug, claim=claim, created_at=at)
            for slug, claim, at in reports
        ],
        ai_requests=[
            ExportAiRequest(problem_slug=slug, level=level, outcome=outcome, created_at=at)
            for slug, level, outcome, at in ai_requests
        ],
        memberships=[
            ExportMembership(organization=name, role=role.value, created_at=at)
            for name, role, at in memberships
        ],
        groups=[
            ExportGroup(organization=org, group=group, joined_at=at) for org, group, at in groups
        ],
        attendance=[
            ExportAttendance(group=group, starts_at=starts, title=title, status=status.value)
            for group, starts, title, status in attendance
        ],
    )


def _is_last_org_admin(db: Session, user: User) -> bool:
    org_ids = db.scalars(
        select(OrgMembership.org_id).where(
            OrgMembership.user_id == user.id, OrgMembership.role == OrgRole.org_admin
        )
    ).all()
    for org_id in org_ids:
        admins = db.scalar(
            select(func.count()).where(
                OrgMembership.org_id == org_id, OrgMembership.role == OrgRole.org_admin
            )
        )
        if admins == 1:
            return True
    return False


def delete_account(db: Session, user: User, password: str, ip: str | None) -> None:
    """Remove the account; foreign keys remove or unlink everything keyed to it."""
    if not verify_password(user.password_hash, password):
        raise ApiError(401, "invalid_credentials", "Password is incorrect.")
    if user.role == Role.admin:
        raise ApiError(409, "admin_cannot_delete_self", "Platform admins cannot delete themselves.")
    if _is_last_org_admin(db, user):
        raise ApiError(409, "last_org_admin", "An organization needs at least one admin.")
    audit.record(
        db,
        action="user.deleted",
        actor_id=None,  # the row outlives the account
        target_type="user",
        target_id=user.id,
        details={"via": "self"},
        ip=ip,
    )
    db.execute(delete(User).where(User.id == user.id))
