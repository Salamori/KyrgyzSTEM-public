import uuid
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.rate_limit import limiter
from app.modules.auth.deps import client_ip, require_admin, require_user
from app.modules.orgs import service
from app.modules.orgs.models import OrgRole
from app.modules.orgs.permissions import org_context
from app.modules.orgs.schemas import (
    AdminAssign,
    AttendanceRow,
    AttendanceUpdate,
    GroupCreate,
    GroupMemberOut,
    GroupSummary,
    GroupUpdate,
    JoinCodeOut,
    JoinedOut,
    JoinRequest,
    MemberOut,
    MyAttendanceRow,
    OrgAdminView,
    OrgCreate,
    OrgGroupOut,
    OrgOut,
    SessionCreate,
    SessionOut,
    StaffAdd,
)
from app.modules.users.models import User

router = APIRouter(tags=["organizations"])


# ------------------------------------------------------------------ platform admin


@router.get("/admin/orgs")
def admin_orgs(
    _: User = Depends(require_admin), db: Session = Depends(get_db)
) -> list[OrgAdminView]:
    return service.admin_overview(db)


@router.post("/admin/orgs", status_code=status.HTTP_201_CREATED)
def admin_create_org(
    data: OrgCreate,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> OrgAdminView:
    org = service.create_org(db, admin, data, client_ip(request))
    db.commit()
    return next(o for o in service.admin_overview(db) if o.id == org.id)


@router.post("/admin/orgs/{org_id}/admins", status_code=status.HTTP_204_NO_CONTENT)
def admin_assign_org_admin(
    org_id: uuid.UUID,
    data: AdminAssign,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> None:
    service.assign_org_admin(db, admin, org_id, data.email, client_ip(request))
    db.commit()


# ------------------------------------------------------------------ members of an organization


@router.get("/orgs")
def my_orgs(user: User = Depends(require_user), db: Session = Depends(get_db)) -> list[OrgOut]:
    return service.my_orgs(db, user)


@router.get("/orgs/{org_id}")
def get_org(
    org_id: uuid.UUID, user: User = Depends(require_user), db: Session = Depends(get_db)
) -> OrgOut:
    return service.org_out(org_context(db, org_id, user))


@router.get("/orgs/{org_id}/members")
def list_members(
    org_id: uuid.UUID, user: User = Depends(require_user), db: Session = Depends(get_db)
) -> list[MemberOut]:
    return service.list_members(db, org_context(db, org_id, user))


@router.post("/orgs/{org_id}/members", status_code=status.HTTP_201_CREATED)
def add_staff(
    org_id: uuid.UUID,
    data: StaffAdd,
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> MemberOut:
    member = service.add_staff(
        db, org_context(db, org_id, user), data.email, OrgRole(data.role), client_ip(request)
    )
    db.commit()
    return member


@router.delete("/orgs/{org_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_member(
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> None:
    service.remove_member(db, org_context(db, org_id, user), user_id, client_ip(request))
    db.commit()


# ------------------------------------------------------------------ groups


@router.get("/orgs/{org_id}/groups")
def list_groups(
    org_id: uuid.UUID, user: User = Depends(require_user), db: Session = Depends(get_db)
) -> list[OrgGroupOut]:
    return service.list_groups(db, org_context(db, org_id, user))


@router.post("/orgs/{org_id}/groups", status_code=status.HTTP_201_CREATED)
def create_group(
    org_id: uuid.UUID,
    data: GroupCreate,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> OrgGroupOut:
    group = service.create_group(db, org_context(db, org_id, user), data)
    db.commit()
    return group


@router.get("/orgs/{org_id}/groups/{group_id}")
def get_group(
    org_id: uuid.UUID,
    group_id: uuid.UUID,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> OrgGroupOut:
    ctx = org_context(db, org_id, user)
    return service.group_detail(db, ctx, service.visible_group(db, ctx, group_id))


@router.patch("/orgs/{org_id}/groups/{group_id}")
def rename_group(
    org_id: uuid.UUID,
    group_id: uuid.UUID,
    data: GroupUpdate,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> OrgGroupOut:
    group = service.rename_group(db, org_context(db, org_id, user), group_id, data.name)
    db.commit()
    return group


@router.delete("/orgs/{org_id}/groups/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_group(
    org_id: uuid.UUID,
    group_id: uuid.UUID,
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> None:
    service.delete_group(db, org_context(db, org_id, user), group_id, client_ip(request))
    db.commit()


@router.post("/orgs/{org_id}/groups/{group_id}/join-code")
def new_join_code(
    org_id: uuid.UUID,
    group_id: uuid.UUID,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> JoinCodeOut:
    code = service.new_join_code(db, org_context(db, org_id, user), group_id)
    db.commit()
    return code


@router.post("/groups/join")
def join_group(
    data: JoinRequest,
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> JoinedOut:
    # Codes are short, so guessing is throttled per user and per IP.
    limiter.hit(f"join:{user.id}", limit=10, window_seconds=3600)
    limiter.hit(f"join-ip:{client_ip(request)}", limit=30, window_seconds=3600)
    joined = service.join(db, user, data.code)
    db.commit()
    return joined


@router.get("/orgs/{org_id}/groups/{group_id}/members")
def group_members(
    org_id: uuid.UUID,
    group_id: uuid.UUID,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> list[GroupMemberOut]:
    return service.group_members(db, org_context(db, org_id, user), group_id)


@router.delete(
    "/orgs/{org_id}/groups/{group_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT
)
def remove_group_member(
    org_id: uuid.UUID,
    group_id: uuid.UUID,
    user_id: uuid.UUID,
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> None:
    service.remove_group_member(
        db, org_context(db, org_id, user), group_id, user_id, client_ip(request)
    )
    db.commit()


# ------------------------------------------------------------------ sessions & attendance


@router.get("/orgs/{org_id}/groups/{group_id}/sessions")
def list_sessions(
    org_id: uuid.UUID,
    group_id: uuid.UUID,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> list[SessionOut]:
    return service.list_sessions(db, org_context(db, org_id, user), group_id)


@router.post("/orgs/{org_id}/groups/{group_id}/sessions", status_code=status.HTTP_201_CREATED)
def create_session(
    org_id: uuid.UUID,
    group_id: uuid.UUID,
    data: SessionCreate,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> SessionOut:
    ctx = org_context(db, org_id, user)
    session_id = service.create_session(db, ctx, group_id, data)
    db.commit()
    return next(s for s in service.list_sessions(db, ctx, group_id) if s.id == session_id)


@router.delete(
    "/orgs/{org_id}/groups/{group_id}/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT
)
def delete_session(
    org_id: uuid.UUID,
    group_id: uuid.UUID,
    session_id: uuid.UUID,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> None:
    service.delete_session(db, org_context(db, org_id, user), group_id, session_id)
    db.commit()


@router.get("/orgs/{org_id}/groups/{group_id}/sessions/{session_id}/attendance")
def get_attendance(
    org_id: uuid.UUID,
    group_id: uuid.UUID,
    session_id: uuid.UUID,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> list[AttendanceRow]:
    return service.attendance(db, org_context(db, org_id, user), group_id, session_id)


@router.put("/orgs/{org_id}/groups/{group_id}/sessions/{session_id}/attendance")
def mark_attendance(
    org_id: uuid.UUID,
    group_id: uuid.UUID,
    session_id: uuid.UUID,
    data: AttendanceUpdate,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> list[AttendanceRow]:
    rows = service.mark_attendance(db, org_context(db, org_id, user), group_id, session_id, data)
    db.commit()
    return rows


@router.get("/orgs/{org_id}/groups/{group_id}/summary")
def group_summary(
    org_id: uuid.UUID,
    group_id: uuid.UUID,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> GroupSummary:
    return service.summary(db, org_context(db, org_id, user), group_id)


@router.get(
    "/orgs/{org_id}/groups/{group_id}/attendance.csv",
    response_class=Response,
    responses={200: {"content": {"text/csv": {}}}},
)
def export_attendance(
    org_id: uuid.UUID,
    group_id: uuid.UUID,
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> Response:
    body, filename = service.export_csv(
        db, org_context(db, org_id, user), group_id, client_ip(request)
    )
    db.commit()  # the export itself is audited
    # Headers are Latin-1: ASCII fallback plus the UTF-8 name (RFC 6266 / RFC 5987).
    fallback = "".join(ch if ch.isascii() else "-" for ch in filename)
    return Response(
        content=body,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": (
                f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(filename)}"
            )
        },
    )


@router.get("/me/attendance")
def my_attendance(
    user: User = Depends(require_user), db: Session = Depends(get_db)
) -> list[MyAttendanceRow]:
    return service.my_attendance(db, user)
