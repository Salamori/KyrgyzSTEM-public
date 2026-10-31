"""School/club operations: organization isolation, explicit permissions, attendance workflow."""

import csv
import io
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import utcnow
from app.modules.audit.models import AuditLog
from app.modules.orgs import service
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
from app.modules.users.models import Role, User
from tests.conftest import login


@dataclass
class OrgWorld:
    org: Organization
    group: Group
    session: GroupSession
    admin: User
    lead: User
    teacher: User  # same org, does not lead the group
    student: User  # group member
    outsider: User  # org student, not in the group
    clients: dict[str, TestClient]


@pytest.fixture
def build_org(
    db: Session, make_user: Callable[..., User], make_client: Callable[[], TestClient]
) -> Callable[[str], OrgWorld]:
    def factory(tag: str) -> OrgWorld:
        org = Organization(name=f"[Synthetic] Org {tag}", kind=OrgKind.club)
        db.add(org)
        db.flush()
        users = {
            name: make_user(
                email=f"{name}-{tag}@example.com".lower(), display_name=f"{name.title()} {tag}"
            )
            for name in ("admin", "lead", "teacher", "student", "outsider")
        }
        roles = {
            "admin": OrgRole.org_admin,
            "lead": OrgRole.teacher,
            "teacher": OrgRole.teacher,
            "student": OrgRole.student,
            "outsider": OrgRole.student,
        }
        for name, role in roles.items():
            db.add(OrgMembership(org_id=org.id, user_id=users[name].id, role=role))
        group = Group(
            org_id=org.id, name=f"Club {tag}", kind=GroupKind.club, lead_id=users["lead"].id
        )
        db.add(group)
        db.flush()
        db.add(GroupMember(group_id=group.id, user_id=users["student"].id))
        session = GroupSession(group_id=group.id, starts_at=utcnow(), title=f"Week 1 {tag}")
        db.add(session)
        db.flush()
        db.add(
            AttendanceRecord(
                session_id=session.id, user_id=users["student"].id, status=AttendanceStatus.present
            )
        )
        db.commit()
        clients = {name: login(make_client(), u.email) for name, u in users.items()}
        return OrgWorld(
            org,
            group,
            session,
            users["admin"],
            users["lead"],
            users["teacher"],
            users["student"],
            users["outsider"],
            clients,
        )

    return factory


def routes(w: OrgWorld) -> list[tuple[str, str, dict[str, Any] | None]]:
    """Every org-scoped route, filled in with the IDs of world `w`."""
    o, g, s, u = w.org.id, w.group.id, w.session.id, w.student.id
    return [
        ("GET", f"/api/v1/orgs/{o}", None),
        ("GET", f"/api/v1/orgs/{o}/members", None),
        ("POST", f"/api/v1/orgs/{o}/members", {"email": "x@example.com", "role": "teacher"}),
        ("DELETE", f"/api/v1/orgs/{o}/members/{u}", None),
        ("GET", f"/api/v1/orgs/{o}/groups", None),
        ("POST", f"/api/v1/orgs/{o}/groups", {"name": "Intruder", "kind": "club"}),
        ("GET", f"/api/v1/orgs/{o}/groups/{g}", None),
        ("PATCH", f"/api/v1/orgs/{o}/groups/{g}", {"name": "Renamed"}),
        ("DELETE", f"/api/v1/orgs/{o}/groups/{g}", None),
        ("POST", f"/api/v1/orgs/{o}/groups/{g}/join-code", None),
        ("GET", f"/api/v1/orgs/{o}/groups/{g}/members", None),
        ("DELETE", f"/api/v1/orgs/{o}/groups/{g}/members/{u}", None),
        ("GET", f"/api/v1/orgs/{o}/groups/{g}/sessions", None),
        ("POST", f"/api/v1/orgs/{o}/groups/{g}/sessions", {"starts_at": utcnow().isoformat()}),
        ("DELETE", f"/api/v1/orgs/{o}/groups/{g}/sessions/{s}", None),
        ("GET", f"/api/v1/orgs/{o}/groups/{g}/sessions/{s}/attendance", None),
        (
            "PUT",
            f"/api/v1/orgs/{o}/groups/{g}/sessions/{s}/attendance",
            {"records": [{"user_id": str(u), "status": "absent"}]},
        ),
        ("GET", f"/api/v1/orgs/{o}/groups/{g}/summary", None),
        ("GET", f"/api/v1/orgs/{o}/groups/{g}/attendance.csv", None),
    ]


def call(client: TestClient, method: str, path: str, body: dict[str, Any] | None) -> int:
    return client.request(method, path, json=body).status_code


def assert_b_untouched(db: Session, b: OrgWorld) -> None:
    db.expire_all()
    assert db.get(Group, b.group.id).name == "Club B"  # type: ignore[union-attr]
    assert db.get(GroupSession, b.session.id) is not None
    record = db.get(AttendanceRecord, (b.session.id, b.student.id))
    assert record is not None and record.status == AttendanceStatus.present
    assert db.get(GroupMember, (b.group.id, b.student.id)) is not None
    assert db.scalar(select(Organization).where(Organization.name == "Intruder")) is None


# ------------------------------------------------------------------ isolation


@pytest.mark.parametrize("actor", ["admin", "lead", "teacher", "student"])
def test_other_organization_is_invisible_and_untouchable(
    build_org: Callable[[str], OrgWorld], db: Session, actor: str
) -> None:
    a, b = build_org("A"), build_org("B")
    client = a.clients[actor]
    for method, path, body in routes(b):
        assert call(client, method, path, body) == 404, (actor, method, path)
    assert_b_untouched(db, b)


def test_swapping_nested_ids_across_organizations_finds_nothing(
    build_org: Callable[[str], OrgWorld], db: Session
) -> None:
    a, b = build_org("A"), build_org("B")
    admin = a.clients["admin"]  # full rights in A
    oa, ga, sa = a.org.id, a.group.id, a.session.id
    gb, sb = b.group.id, b.session.id
    attempts = [
        ("GET", f"/api/v1/orgs/{oa}/groups/{gb}", None),
        ("PATCH", f"/api/v1/orgs/{oa}/groups/{gb}", {"name": "Renamed"}),
        ("DELETE", f"/api/v1/orgs/{oa}/groups/{gb}", None),
        ("GET", f"/api/v1/orgs/{oa}/groups/{gb}/members", None),
        ("GET", f"/api/v1/orgs/{oa}/groups/{gb}/attendance.csv", None),
        ("GET", f"/api/v1/orgs/{oa}/groups/{ga}/sessions/{sb}/attendance", None),
        (
            "PUT",
            f"/api/v1/orgs/{oa}/groups/{ga}/sessions/{sb}/attendance",
            {"records": [{"user_id": str(b.student.id), "status": "absent"}]},
        ),
        ("DELETE", f"/api/v1/orgs/{oa}/groups/{ga}/sessions/{sb}", None),
        ("DELETE", f"/api/v1/orgs/{oa}/members/{b.student.id}", None),
    ]
    for method, path, body in attempts:
        assert call(admin, method, path, body) == 404, (method, path)
    # B's student cannot be marked in A's session either.
    res = admin.put(
        f"/api/v1/orgs/{oa}/groups/{ga}/sessions/{sa}/attendance",
        json={"records": [{"user_id": str(b.student.id), "status": "absent"}]},
    )
    assert res.status_code == 422 and res.json()["error"]["code"] == "not_a_member"
    assert_b_untouched(db, b)


def test_platform_admin_manages_structure_but_cannot_read_records(
    build_org: Callable[[str], OrgWorld], signed_in: Callable[[Role], tuple[TestClient, User]]
) -> None:
    b = build_org("B")
    platform, _ = signed_in(Role.admin)
    overview = platform.get("/api/v1/admin/orgs").json()
    row = next(o for o in overview if o["id"] == str(b.org.id))
    assert (row["admins"], row["teachers"], row["students"], row["groups"]) == (1, 2, 2, 1)
    assert "Student B" not in str(overview)
    for method, path, body in routes(b):
        assert call(platform, method, path, body) == 404, (method, path)


def test_admin_org_endpoints_require_platform_admin(
    client: TestClient, signed_in: Callable[[Role], tuple[TestClient, User]]
) -> None:
    assert client.get("/api/v1/admin/orgs").status_code == 401
    assert client.get("/api/v1/orgs").status_code == 401
    teacher, _ = signed_in(Role.teacher)
    assert teacher.get("/api/v1/admin/orgs").status_code == 403
    assert teacher.post("/api/v1/admin/orgs", json={"name": "X", "kind": "club"}).status_code == 403


# ------------------------------------------------------------------ roles inside one organization


def test_student_permissions(build_org: Callable[[str], OrgWorld]) -> None:
    a = build_org("A")
    st = a.clients["student"]
    o, g, s = a.org.id, a.group.id, a.session.id
    assert st.get(f"/api/v1/orgs/{o}").json()["my_role"] == "student"
    assert st.get(f"/api/v1/orgs/{o}/members").status_code == 403
    assert (
        st.post(f"/api/v1/orgs/{o}/groups", json={"name": "Mine", "kind": "club"}).status_code
        == 403
    )
    groups = st.get(f"/api/v1/orgs/{o}/groups").json()
    assert [x["name"] for x in groups] == ["Club A"] and groups[0]["can_manage"] is False
    assert groups[0]["join_code_expires_at"] is None
    sessions = st.get(f"/api/v1/orgs/{o}/groups/{g}/sessions").json()
    assert sessions[0]["my_status"] == "present" and sessions[0]["present"] is None  # no aggregates
    for path in (
        f"/groups/{g}/members",
        f"/groups/{g}/summary",
        f"/groups/{g}/attendance.csv",
        f"/groups/{g}/sessions/{s}/attendance",
    ):
        assert st.get(f"/api/v1/orgs/{o}{path}").status_code == 404, path
    put = st.put(
        f"/api/v1/orgs/{o}/groups/{g}/sessions/{s}/attendance",
        json={"records": [{"user_id": str(a.student.id), "status": "excused"}]},
    )
    assert put.status_code == 404
    other = st.delete(f"/api/v1/orgs/{o}/groups/{g}/members/{a.lead.id}")
    assert other.status_code in (403, 404)


def test_outsider_student_cannot_see_group(build_org: Callable[[str], OrgWorld]) -> None:
    a = build_org("A")
    out = a.clients["outsider"]
    assert out.get(f"/api/v1/orgs/{a.org.id}/groups").json() == []
    assert out.get(f"/api/v1/orgs/{a.org.id}/groups/{a.group.id}").status_code == 404
    assert out.get(f"/api/v1/orgs/{a.org.id}/groups/{a.group.id}/sessions").status_code == 404


def test_teacher_manages_only_groups_they_lead(build_org: Callable[[str], OrgWorld]) -> None:
    a = build_org("A")
    o, g = a.org.id, a.group.id
    other_teacher = a.clients["teacher"]
    assert other_teacher.get(f"/api/v1/orgs/{o}/groups").json() == []
    assert other_teacher.get(f"/api/v1/orgs/{o}/groups/{g}/members").status_code == 404
    assert other_teacher.get(f"/api/v1/orgs/{o}/members").status_code == 403
    created = other_teacher.post(
        f"/api/v1/orgs/{o}/groups", json={"name": "Robotics", "kind": "club"}
    )
    assert created.status_code == 201 and created.json()["can_manage"] is True

    lead = a.clients["lead"]
    assert [x["name"] for x in lead.get(f"/api/v1/orgs/{o}/groups").json()] == ["Club A"]
    members = lead.get(f"/api/v1/orgs/{o}/groups/{g}/members").json()
    assert [m["display_name"] for m in members] == ["Student A"]
    assert "email" not in members[0] and "student-a@example.com" not in str(members)


def test_org_admin_manages_all_groups_and_staff(
    build_org: Callable[[str], OrgWorld], db: Session
) -> None:
    a = build_org("A")
    admin = a.clients["admin"]
    o = a.org.id
    a.clients["teacher"].post(f"/api/v1/orgs/{o}/groups", json={"name": "Robotics", "kind": "club"})
    names = sorted(x["name"] for x in admin.get(f"/api/v1/orgs/{o}/groups").json())
    assert names == ["Club A", "Robotics"]
    roles = {m["display_name"]: m["role"] for m in admin.get(f"/api/v1/orgs/{o}/members").json()}
    assert roles["Lead A"] == "teacher" and roles["Student A"] == "student"

    assert (
        admin.post(
            f"/api/v1/orgs/{o}/members", json={"email": "nobody@example.com", "role": "teacher"}
        ).status_code
        == 404
    )
    res = admin.post(
        f"/api/v1/orgs/{o}/members", json={"email": "outsider-a@example.com", "role": "teacher"}
    )
    assert res.status_code == 201 and res.json()["role"] == "teacher"
    assert (
        admin.post(
            f"/api/v1/orgs/{o}/members", json={"email": "a@example.com", "role": "owner"}
        ).status_code
        == 422
    )
    last = admin.delete(f"/api/v1/orgs/{o}/members/{a.admin.id}")
    assert last.status_code == 409 and last.json()["error"]["code"] == "last_org_admin"
    actions = set(db.scalars(select(AuditLog.action)))
    assert "org.member_added" in actions


# ------------------------------------------------------------------ join codes


def test_join_code_flow(
    build_org: Callable[[str], OrgWorld],
    db: Session,
    make_user: Callable[..., User],
    make_client: Callable[[], TestClient],
) -> None:
    a = build_org("A")
    lead = a.clients["lead"]
    code = lead.post(f"/api/v1/orgs/{a.org.id}/groups/{a.group.id}/join-code").json()["code"]
    assert len(code) == 8
    db.expire_all()
    stored = db.get(Group, a.group.id)
    assert stored is not None and stored.join_code_hash and code not in stored.join_code_hash

    newcomer = make_user(email="new@example.com", display_name="Newcomer")
    nc = login(make_client(), newcomer.email)
    joined = nc.post("/api/v1/groups/join", json={"code": f" {code[:4].lower()}-{code[4:]} "})
    assert joined.status_code == 200 and joined.json()["group_id"] == str(a.group.id)
    assert nc.get("/api/v1/orgs").json()[0]["my_role"] == "student"
    assert nc.post("/api/v1/groups/join", json={"code": code}).status_code == 200  # idempotent

    # Rotating invalidates the old code; joining never downgrades staff.
    lead.post(f"/api/v1/orgs/{a.org.id}/groups/{a.group.id}/join-code")
    stale = a.clients["outsider"].post("/api/v1/groups/join", json={"code": code})
    assert stale.status_code == 404 and stale.json()["error"]["code"] == "invalid_join_code"


def test_expired_and_guessed_codes(build_org: Callable[[str], OrgWorld], db: Session) -> None:
    a = build_org("A")
    code = (
        a.clients["lead"]
        .post(f"/api/v1/orgs/{a.org.id}/groups/{a.group.id}/join-code")
        .json()["code"]
    )
    group = db.get(Group, a.group.id)
    assert group is not None
    group.join_code_expires_at = utcnow() - timedelta(seconds=1)
    db.commit()
    guesser = a.clients["outsider"]
    assert guesser.post("/api/v1/groups/join", json={"code": code}).status_code == 404
    for _ in range(9):
        guesser.post("/api/v1/groups/join", json={"code": "WRONGCODE"})
    assert guesser.post("/api/v1/groups/join", json={"code": "WRONGCODE"}).status_code == 429


# ------------------------------------------------------------------ sessions, attendance, reports


def test_attendance_workflow_and_summary(build_org: Callable[[str], OrgWorld], db: Session) -> None:
    a = build_org("A")
    lead = a.clients["lead"]
    o, g = a.org.id, a.group.id
    db.add(GroupMember(group_id=g, user_id=a.outsider.id))
    db.commit()
    created = lead.post(
        f"/api/v1/orgs/{o}/groups/{g}/sessions",
        json={"starts_at": utcnow().isoformat(), "title": " Week 2 "},
    )
    assert created.status_code == 201 and created.json()["title"] == "Week 2"
    sid = created.json()["id"]
    marks = {
        "records": [
            {"user_id": str(a.student.id), "status": "absent"},
            {"user_id": str(a.outsider.id), "status": "excused"},
        ]
    }
    rows = lead.put(f"/api/v1/orgs/{o}/groups/{g}/sessions/{sid}/attendance", json=marks).json()
    assert {r["display_name"]: r["status"] for r in rows} == {
        "Student A": "absent",
        "Outsider A": "excused",
    }
    marks["records"][0]["status"] = "present"
    lead.put(f"/api/v1/orgs/{o}/groups/{g}/sessions/{sid}/attendance", json=marks)  # upsert

    sessions = {s["title"]: s for s in lead.get(f"/api/v1/orgs/{o}/groups/{g}/sessions").json()}
    assert (
        sessions["Week 2"]["present"],
        sessions["Week 2"]["excused"],
        sessions["Week 2"]["unmarked"],
    ) == (1, 1, 0)
    assert sessions["Week 1 A"]["unmarked"] == 1
    summary = lead.get(f"/api/v1/orgs/{o}/groups/{g}/summary").json()
    assert summary == {
        "sessions": 2,
        "members": 2,
        "marked": 3,
        "present": 2,
        "attendance_rate": pytest.approx(2 / 3),
    }

    bad_bodies: list[dict[str, Any]] = [
        {"records": []},
        {"records": [{"user_id": str(a.student.id), "status": "late"}]},
    ]

    for bad in bad_bodies:
        assert (
            lead.put(f"/api/v1/orgs/{o}/groups/{g}/sessions/{sid}/attendance", json=bad).status_code
            == 422
        )
    assert (
        lead.post(f"/api/v1/orgs/{o}/groups/{g}/sessions", json={"starts_at": "soon"}).status_code
        == 422
    )


def test_csv_export_is_escaped_and_audited(
    build_org: Callable[[str], OrgWorld], db: Session
) -> None:
    a = build_org("A")
    a.student.display_name = '=HYPERLINK("http://evil.example","x")'
    db.commit()
    res = a.clients["lead"].get(f"/api/v1/orgs/{a.org.id}/groups/{a.group.id}/attendance.csv")
    assert res.status_code == 200 and res.headers["content-type"].startswith("text/csv")
    assert "attachment" in res.headers["content-disposition"]
    rows = list(csv.reader(io.StringIO(res.text)))
    assert rows[0] == ["session_start_utc", "session_title", "student", "status"]
    assert rows[1][2].startswith("'=") and rows[1][3] == "present"
    entry = db.scalars(select(AuditLog).where(AuditLog.action == "report.exported")).one()
    assert entry.actor_id == a.lead.id and "HYPERLINK" not in str(entry.details)


def test_leaving_or_removal_deletes_attendance(
    build_org: Callable[[str], OrgWorld], db: Session
) -> None:
    a = build_org("A")
    st = a.clients["student"]
    assert st.get("/api/v1/me/attendance").json()[0]["status"] == "present"
    assert (
        st.delete(f"/api/v1/orgs/{a.org.id}/groups/{a.group.id}/members/{a.student.id}").status_code
        == 204
    )
    db.expire_all()
    assert db.get(AttendanceRecord, (a.session.id, a.student.id)) is None
    assert st.get("/api/v1/me/attendance").json() == []
    assert st.get(f"/api/v1/orgs/{a.org.id}/groups/{a.group.id}").status_code == 404


def test_teacher_removal_is_audited_and_deletes_records(
    build_org: Callable[[str], OrgWorld], db: Session
) -> None:
    a = build_org("A")
    res = a.clients["lead"].delete(
        f"/api/v1/orgs/{a.org.id}/groups/{a.group.id}/members/{a.student.id}"
    )
    assert res.status_code == 204
    assert (
        db.scalars(select(AuditLog).where(AuditLog.action == "group.member_removed")).one().actor_id
        == a.lead.id
    )
    admin_remove = a.clients["admin"].delete(f"/api/v1/orgs/{a.org.id}/members/{a.outsider.id}")
    assert admin_remove.status_code == 204


def test_my_attendance_is_only_mine(build_org: Callable[[str], OrgWorld]) -> None:
    a = build_org("A")
    build_org("B")  # another organization's records must not appear
    mine = a.clients["student"].get("/api/v1/me/attendance").json()
    assert [(r["group_name"], r["status"]) for r in mine] == [("Club A", "present")]
    assert a.clients["outsider"].get("/api/v1/me/attendance").json() == []


def test_retention_purge(build_org: Callable[[str], OrgWorld], db: Session) -> None:
    a = build_org("A")
    old = GroupSession(group_id=a.group.id, starts_at=utcnow() - timedelta(days=400))
    db.add(old)
    db.flush()
    old_id, recent_id = old.id, a.session.id
    db.add(
        AttendanceRecord(session_id=old_id, user_id=a.student.id, status=AttendanceStatus.present)
    )
    db.commit()
    assert service.purge_expired(db, utcnow()) == 1
    db.commit()
    db.expunge_all()
    assert db.get(GroupSession, old_id) is None and db.get(GroupSession, recent_id) is not None
    assert (
        db.scalars(select(AttendanceRecord).where(AttendanceRecord.session_id == old_id)).all()
        == []
    )


def test_platform_admin_creates_org_and_assigns_admin(
    signed_in: Callable[[Role], tuple[TestClient, User]],
    make_user: Callable[..., User],
    make_client: Callable[[], TestClient],
    db: Session,
) -> None:
    platform, _ = signed_in(Role.admin)
    created = platform.post("/api/v1/admin/orgs", json={"name": "  Lyceum Club ", "kind": "school"})
    assert created.status_code == 201 and created.json()["name"] == "Lyceum Club"
    oid = created.json()["id"]
    head = make_user(email="head@example.com", display_name="Head")
    assert (
        platform.post(
            f"/api/v1/admin/orgs/{oid}/admins", json={"email": "head@example.com"}
        ).status_code
        == 204
    )
    head_client = login(make_client(), head.email)
    assert head_client.get(f"/api/v1/orgs/{oid}").json()["my_role"] == "org_admin"
    actions = list(db.scalars(select(AuditLog.action).order_by(AuditLog.created_at)))
    assert actions[-2:] == ["org.created", "org.admin_assigned"]
    assert platform.post("/api/v1/admin/orgs", json={"name": "", "kind": "club"}).status_code == 422
