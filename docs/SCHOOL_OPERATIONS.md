# School and club operations

Status: first workflow implemented (club session attendance). Everything else in the
School Operations scope (events and registrations, schedules, volunteer tutoring,
assignment statistics) is **not built yet** and is listed at the end.

## 1. Workflow chosen with the owner (2026-10-25)

| Question | Owner's answer |
|---|---|
| First workflow | Club session attendance: a teacher or club lead runs a recurring session (e.g. a weekly olympiad club) and records who attended |
| How it is done today | **Not done yet** — a new process, so there is no current practice to copy and no existing records to migrate |
| Who sees individual records | The group's teacher/lead; the organization's admin; each student sees only their own |
| How students join | A short, expiring join code shared by the teacher |

Because there is no current process, the steps below are a **proposed** process. They must
be checked with the first real school or club before a pilot; until then all demo data is
synthetic.

## 2. Target process (smallest useful version)

1. A platform admin creates the organization (school or club) and names its first
   organization admin by their account email.
2. The organization admin adds teachers (by account email).
3. A teacher creates a group (a class or a club) and becomes its lead.
4. The teacher generates a join code (8 characters, valid 7 days) and shares it in person
   or in the group's usual channel.
5. Students sign in and enter the code; they become members of the group.
6. For each meeting the teacher creates a session (date/time, optional short title) and
   marks each member present, absent or excused — on a phone, one tap per student.
7. The teacher or organization admin sees an aggregate summary and can download a CSV.
8. Students see their own attendance history and can leave a group.

## 3. Roles and explicit permissions

Organization roles are separate from the platform roles (`student`/`teacher`/`admin`).
A **platform admin** manages organizations and organization admins but **cannot read
attendance or rosters** unless they are also a member of that organization.

| Permission | org_admin | teacher | student |
|---|:-:|:-:|:-:|
| View the organization (name, own role) | ✓ | ✓ | ✓ |
| List and manage staff members | ✓ | | |
| Create groups (becomes lead) | ✓ | ✓ | |
| View / manage **all** groups in the organization | ✓ | | |
| View / manage groups they lead | ✓ | ✓ | |
| Generate join codes, remove members, create sessions, mark attendance, export CSV — for manageable groups | ✓ | ✓ | |
| Join a group with a code; view own attendance; leave a group | ✓ | ✓ | ✓ |

Enforced server-side in `app/modules/orgs/permissions.py`; every route resolves the
organization from the URL **and** checks that nested resources belong to it, so changing
an ID in a request returns `404`, never another organization's data.

## 4. Data collected (minimized)

- Organization: name, kind (school/club/other).
- Group: name, kind (class/club), lead.
- Membership: user, role, join time.
- Session: start time, optional title (≤ 80 chars), who created it.
- Attendance: status (present/absent/excused), who marked it, when.

Not collected: student lists from schools, contact details, grades, location, device or
behavioural data. Teachers see students' **display names only**, never their emails.

## 5. Retention and deletion

| Data | Policy | Mechanism |
|---|---|---|
| Attendance and sessions | Kept for `ATTENDANCE_RETENTION_DAYS` (default 365) after the session, then deleted | `uv run python -m scripts.purge_retention` (run daily, e.g. cron) |
| A student leaves a group, or is removed | Their attendance records for that group are deleted at once | Service + test |
| A group is deleted | Its sessions, attendance and memberships are deleted | Database cascade |
| An organization is deleted | Everything in it is deleted | Database cascade |
| Account deletion | Cascades to memberships and attendance | Database cascade (self-service deletion is planned, Phase 6) |
| Audit log | Records who changed memberships or exported reports; kept without student names | Append-only |

The deploying school must confirm these periods against its own rules and local law
before a pilot. This document is not legal advice.

## 6. What works, what needs others

| Feature | Status |
|---|---|
| Organizations, staff, groups, join codes, sessions, attendance, summary, CSV, audit | Functional (tested; synthetic data) |
| Mobile teacher views | Functional (checked at 375 px) |
| Retention purge | Functional as a script; scheduling it is a deployment step |
| Events, registrations, schedules | Not built |
| Volunteer tutoring sessions and sign-ups | Not built |
| Assignment distribution and completion statistics | Not built; depends on proposal 0002 (problem sets) |
| External integrations | None needed for this workflow (no school information system, no messaging) |
| Requires a real school or club | Validating the process, retention periods, who may be org admin, consent for minors, and approval before any real student data or pilot |
