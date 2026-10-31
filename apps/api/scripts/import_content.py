"""Import content/curriculum.toml and content/problems/* into the database.

Usage (from apps/api), one of the two author options is required:
    uv run python -m scripts.import_content --author-email EMAIL [slug ...]
    uv run python -m scripts.import_content --author-unlinked [slug ...]

Every problem is verified first (samples and a brute-force cross-check). New problems are
created `in_review`; publishing needs a human reviewer through the admin API.

Whenever the author is known and has an account, use `--author-email`: it links the
imported problems to that account, so the API refuses to let that person confirm rights,
review or publish them. `--author-unlinked` is an explicit statement that no account can be
linked (e.g. "KyrgyzSTEM contributors"); reviewers must then attest they are not an author,
which is audited but is not proof of identity (audit F-05, docs/CONTENT_GUIDE.md). There is
no default, so linking cannot be skipped by accident.
"""

import argparse
import sys

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_engine
from app.modules.auth.service import normalize_email
from app.modules.olympiad import content
from app.modules.olympiad.importer import import_curriculum, import_problem
from app.modules.users.models import User


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="scripts.import_content")
    who = parser.add_mutually_exclusive_group(required=True)
    who.add_argument("--author-email", help="link imported problems to the author's account")
    who.add_argument(
        "--author-unlinked",
        action="store_true",
        help="no author account can be linked; reviewers must attest instead (not proof)",
    )
    parser.add_argument("slugs", nargs="*")
    args = parser.parse_args(argv)

    root = get_settings().content_dir
    curriculum = content.load_curriculum(root / "curriculum.toml")
    problems = content.load_problems(root / "problems")
    if args.slugs:
        problems = [p for p in problems if p.slug in set(args.slugs)]
    with Session(get_engine()) as db:
        author = None
        if args.author_email:
            author = db.scalar(
                select(User).where(User.email == normalize_email(args.author_email), User.is_active)
            )
            if author is None:
                print("No active account with that email.", file=sys.stderr)
                return 2
        import_curriculum(db, curriculum)
        db.commit()
        print(f"Curriculum: {len(curriculum.topics)} topics, {len(curriculum.tags)} tags")
        failures = 0
        for problem in problems:
            try:
                result = import_problem(db, problem, author)
                db.commit()
                verb = "created" if result.created else "updated"
                linked = ", author linked" if author else ", author NOT linked (attestation)"
                print(f"  {problem.slug}: {verb}, {result.tests} tests{linked}")
            except content.ContentError as exc:
                db.rollback()
                failures += 1
                print(f"  {problem.slug}: FAILED — {exc}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
