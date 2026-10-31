"""Issue a single-use password reset link token. The only recovery path for platform admins.

An operator with shell access, after confirming who is asking, runs

    python -m scripts.issue_password_reset person@school.example

and opens `/reset-password#token=...` on the site. The token is printed once and only its
hash is stored. Issuing it is written to the audit log (`user.password_reset_issued`,
actor = none, via = cli). Treat the output as a credential: do not keep it in shell history
or logs. Other accounts should be recovered by an admin in the web app instead.
"""

import sys

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models  # noqa: F401  (registers every mapper)
from app.core.db import get_engine
from app.core.errors import ApiError
from app.modules.auth.service import issue_password_reset, normalize_email
from app.modules.users.models import User


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: python -m scripts.issue_password_reset EMAIL", file=sys.stderr)
        return 2
    with Session(get_engine()) as db:
        user = db.scalar(select(User).where(User.email == normalize_email(argv[0])))
        if user is None:
            print("No account with that email.", file=sys.stderr)
            return 1
        try:
            token, expires_at = issue_password_reset(db, user, None, None, "cli")
        except ApiError:
            print("That account is deactivated; nothing changed.", file=sys.stderr)
            return 1
        db.commit()
    print(f"/reset-password#token={token}")
    print(f"Expires at {expires_at.isoformat()}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
