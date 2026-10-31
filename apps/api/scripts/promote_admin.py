"""Make an existing account a platform admin. The only way to create the first admin.

There are no default admin credentials anywhere: the person registers normally through
the web app (choosing their own password), then an operator with shell access runs

    python -m scripts.promote_admin person@school.example

The change is written to the audit log (`user.role_changed`, actor = none, via = cli).
Later admins should be promoted by an existing admin in the web app instead.
"""

import sys

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models  # noqa: F401  (registers every mapper)
from app.core.db import get_engine
from app.modules.audit import service as audit
from app.modules.users.models import Role, User


def promote(db: Session, email: str) -> str:
    user = db.scalar(select(User).where(User.email == email.strip().lower()))
    if user is None:
        return "not_found"
    if not user.is_active:
        return "inactive"
    if user.role == Role.admin:
        return "unchanged"
    audit.record(
        db,
        action="user.role_changed",
        actor_id=None,
        target_type="user",
        target_id=user.id,
        details={"from": user.role.value, "to": Role.admin.value, "via": "cli"},
    )
    user.role = Role.admin
    db.commit()
    return "promoted"


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: python -m scripts.promote_admin EMAIL", file=sys.stderr)
        return 2
    with Session(get_engine()) as db:
        result = promote(db, argv[0])
    messages = {
        "promoted": "Promoted to admin (recorded in the audit log).",
        "unchanged": "Already an admin; nothing changed.",
        "not_found": "No account with that email. Register it in the web app first.",
        "inactive": "That account is deactivated; nothing changed.",
    }
    print(messages[result], file=sys.stderr if result in {"not_found", "inactive"} else None)
    return 0 if result in {"promoted", "unchanged"} else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
