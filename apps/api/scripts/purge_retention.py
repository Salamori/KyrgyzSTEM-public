"""Apply the retention policy (docs/SCHOOL_OPERATIONS.md §5). Run daily, e.g. from cron.

Usage (from apps/api):  uv run python -m scripts.purge_retention
"""

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_engine, utcnow
from app.core.rate_limit import purge_expired_hits
from app.modules.orgs.service import purge_expired


def main() -> int:
    with Session(get_engine()) as db:
        deleted = purge_expired(db, utcnow())
        db.commit()
    days = get_settings().attendance_retention_days
    print(f"Deleted {deleted} session(s) older than {days} days, with their attendance.")
    with get_engine().begin() as conn:
        hits = purge_expired_hits(conn)
    print(f"Deleted {hits} expired rate-limit row(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
