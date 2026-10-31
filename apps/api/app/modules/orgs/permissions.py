"""Explicit organization permissions (docs/SCHOOL_OPERATIONS.md §3).

Rule of thumb: resources outside the caller's organization, or that the caller may not
see, return 404 (no existence leak). Visible resources the caller may not change return 403.
"""

import uuid
from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.modules.orgs.models import Group, GroupSession, Organization, OrgMembership, OrgRole
from app.modules.users.models import User


class Perm(StrEnum):
    view_org = "view_org"
    manage_staff = "manage_staff"
    create_group = "create_group"
    manage_all_groups = "manage_all_groups"
    manage_led_groups = "manage_led_groups"


ROLE_PERMISSIONS: dict[OrgRole, frozenset[Perm]] = {
    OrgRole.org_admin: frozenset(Perm),
    OrgRole.teacher: frozenset({Perm.view_org, Perm.create_group, Perm.manage_led_groups}),
    OrgRole.student: frozenset({Perm.view_org}),
}


@dataclass(frozen=True)
class OrgContext:
    org: Organization
    user: User
    role: OrgRole

    def can(self, perm: Perm) -> bool:
        return perm in ROLE_PERMISSIONS[self.role]

    def require(self, perm: Perm) -> None:
        if not self.can(perm):
            raise ApiError(403, "forbidden", "You do not have permission to do this.")

    def can_manage(self, group: Group) -> bool:
        return self.can(Perm.manage_all_groups) or (
            self.can(Perm.manage_led_groups) and group.lead_id == self.user.id
        )


def _not_found(what: str = "Not found.") -> ApiError:
    return ApiError(404, "not_found", what)


def org_context(db: Session, org_id: uuid.UUID, user: User) -> OrgContext:
    """Membership is required for everything org-scoped, platform admins included."""
    row = db.execute(
        select(Organization, OrgMembership.role)
        .join(OrgMembership, OrgMembership.org_id == Organization.id)
        .where(Organization.id == org_id, OrgMembership.user_id == user.id)
    ).first()
    if row is None:
        raise _not_found("Organization not found.")
    return OrgContext(org=row[0], user=user, role=row[1])


def group_in(db: Session, ctx: OrgContext, group_id: uuid.UUID) -> Group:
    group = db.get(Group, group_id)
    # The group must belong to the org in the URL: swapping IDs across orgs finds nothing.
    if group is None or group.org_id != ctx.org.id:
        raise _not_found("Group not found.")
    return group


def managed_group(db: Session, ctx: OrgContext, group_id: uuid.UUID) -> Group:
    group = group_in(db, ctx, group_id)
    if not ctx.can_manage(group):
        raise _not_found("Group not found.")
    return group


def session_in(db: Session, group: Group, session_id: uuid.UUID) -> GroupSession:
    session = db.get(GroupSession, session_id)
    if session is None or session.group_id != group.id:
        raise _not_found("Session not found.")
    return session
