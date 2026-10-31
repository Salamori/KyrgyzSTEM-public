import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request, status
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.rate_limit import limiter
from app.core.schemas import Page
from app.modules.auth.deps import client_ip, get_optional_user, require_admin, require_user
from app.modules.judge.base import JudgeClient, get_judge
from app.modules.olympiad import service
from app.modules.olympiad.models import Band, ProblemStatus
from app.modules.olympiad.schemas import (
    AdminEditorialOut,
    AdminHintOut,
    DifficultyUpdate,
    EditorialOut,
    EditorialReview,
    HintReview,
    HintsOut,
    ProblemDetail,
    ProblemSummary,
    ProgressOut,
    RightsVerification,
    SelfReportOut,
    SubmissionCreate,
    SubmissionDetail,
    SubmissionSummary,
    TagOut,
    TopicOut,
    UnlockRequest,
)
from app.modules.users.models import Locale, User

router = APIRouter(tags=["olympiad"])

Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0)]
Slug = Annotated[str, Query(max_length=64)]


@router.get("/topics")
def list_topics(
    locale: Locale | None = None,
    user: User | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
) -> list[TopicOut]:
    return service.list_topics(db, service.requested_locale(locale, user))


@router.get("/tags")
def list_tags(
    locale: Locale | None = None,
    user: User | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
) -> list[TagOut]:
    return service.list_tags(db, service.requested_locale(locale, user))


@router.get("/problems")
def list_problems(
    q: Annotated[str | None, Query(max_length=100)] = None,
    topic: Slug | None = None,
    tag: Slug | None = None,
    band: Band | None = None,
    problem_status: Annotated[ProblemStatus | None, Query(alias="status")] = None,
    sort: service.ProblemSort = "band",
    locale: Locale | None = None,
    limit: Limit = 20,
    offset: Offset = 0,
    user: User | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
) -> Page[ProblemSummary]:
    items, total = service.list_problems(
        db,
        user,
        service.requested_locale(locale, user),
        q=q,
        topic=topic,
        tag=tag,
        band=band,
        status=problem_status,
        sort=sort,
        limit=limit,
        offset=offset,
    )
    return Page(items=items, total=total, limit=limit, offset=offset)


@router.get("/problems/{slug}")
def get_problem(
    slug: str,
    locale: Locale | None = None,
    user: User | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
    judge: JudgeClient = Depends(get_judge),
) -> ProblemDetail:
    problem = service.get_problem(db, slug, user)
    return service.problem_detail(
        db, problem, user, service.requested_locale(locale, user), judge.available
    )


@router.get("/problems/{slug}/hints")
def get_hints(
    slug: str,
    locale: Locale | None = None,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> HintsOut:
    problem = service.get_problem(db, slug, user)
    return service.hints(db, problem, user, service.requested_locale(locale, user))


@router.post("/problems/{slug}/hints/{ordinal}/reveal")
def reveal_hint(
    slug: str,
    ordinal: int,
    locale: Locale | None = None,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> HintsOut:
    limiter.hit(f"progress-write:{user.id}", limit=60, window_seconds=60)
    problem = service.get_problem(db, slug, user)
    result = service.reveal_hint(db, problem, user, ordinal, service.requested_locale(locale, user))
    db.commit()
    return result


@router.get("/problems/{slug}/editorial")
def get_editorial(
    slug: str,
    locale: Locale | None = None,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> EditorialOut:
    problem = service.get_problem(db, slug, user)
    result = service.editorial(db, problem, user, service.requested_locale(locale, user))
    db.commit()  # may record an automatic "solved" unlock
    return result


@router.post("/problems/{slug}/editorial/unlock")
def unlock_editorial(
    slug: str,
    _: UnlockRequest,
    locale: Locale | None = None,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> EditorialOut:
    limiter.hit(f"progress-write:{user.id}", limit=60, window_seconds=60)
    problem = service.get_problem(db, slug, user)
    result = service.editorial(
        db, problem, user, service.requested_locale(locale, user), give_up=True
    )
    db.commit()
    return result


@router.post("/problems/{slug}/self-report")
def self_report(
    slug: str, user: User = Depends(require_user), db: Session = Depends(get_db)
) -> SelfReportOut:
    limiter.hit(f"progress-write:{user.id}", limit=60, window_seconds=60)
    problem = service.get_problem(db, slug, user)
    result = service.self_report(db, problem, user)
    db.commit()
    return result


@router.post("/submissions", status_code=status.HTTP_201_CREATED)
def create_submission(
    data: SubmissionCreate,
    locale: Locale | None = None,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
    judge: JudgeClient = Depends(get_judge),
) -> SubmissionDetail:
    limiter.hit(f"submit:{user.id}", limit=6, window_seconds=60)
    submission = service.create_submission(db, user, data, judge.accepts(data.language))
    db.commit()  # the response (queued or not) is built only after the commit succeeded
    return service.submission_detail(db, submission, service.requested_locale(locale, user))


@router.get("/submissions")
def list_submissions(
    problem: Slug | None = None,
    locale: Locale | None = None,
    limit: Limit = 20,
    offset: Offset = 0,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> Page[SubmissionSummary]:
    rows, total = service.list_submissions(db, user, problem, limit, offset)
    loc = service.requested_locale(locale, user)
    return Page(
        items=[service.submission_summary(s, loc) for s in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/submissions/{submission_id}")
def get_submission(
    submission_id: uuid.UUID,
    locale: Locale | None = None,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> SubmissionDetail:
    submission = service.get_submission(db, submission_id, user)
    return service.submission_detail(db, submission, service.requested_locale(locale, user))


@router.get("/me/progress")
def my_progress(
    locale: Locale | None = None,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
    judge: JudgeClient = Depends(get_judge),
) -> ProgressOut:
    return service.progress(db, user, service.requested_locale(locale, user), judge.available)


# ------------------------------------------------------------------ admin review


def _admin_detail(db: Session, slug: str, admin: User, judge: JudgeClient) -> ProblemDetail:
    problem = service.get_problem(db, slug, admin)
    return service.problem_detail(db, problem, admin, admin.preferred_locale, judge.available)


@router.patch("/admin/problems/{slug}/difficulty")
def review_difficulty(
    slug: str,
    data: DifficultyUpdate,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    judge: JudgeClient = Depends(get_judge),
) -> ProblemDetail:
    service.review_difficulty(db, slug, data, admin, client_ip(request))
    db.commit()
    return _admin_detail(db, slug, admin, judge)


@router.post("/admin/problems/{slug}/translations/{locale}/review")
def review_translation(
    slug: str,
    locale: Locale,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    judge: JudgeClient = Depends(get_judge),
) -> ProblemDetail:
    service.review_translation(db, slug, locale, admin, client_ip(request))
    db.commit()
    return _admin_detail(db, slug, admin, judge)


@router.post("/admin/problems/{slug}/rights/verify")
def verify_rights(
    slug: str,
    data: RightsVerification,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    judge: JudgeClient = Depends(get_judge),
) -> ProblemDetail:
    service.verify_rights(db, slug, data, admin, client_ip(request))
    db.commit()
    return _admin_detail(db, slug, admin, judge)


@router.get("/admin/problems/{slug}/editorials")
def admin_editorials(
    slug: str,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> list[AdminEditorialOut]:
    return service.admin_editorials(db, slug)


@router.post("/admin/problems/{slug}/editorials/{locale}/review")
def review_editorial(
    slug: str,
    locale: Locale,
    data: EditorialReview,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    judge: JudgeClient = Depends(get_judge),
) -> ProblemDetail:
    service.review_editorial(db, slug, locale, data, admin, client_ip(request))
    db.commit()
    return _admin_detail(db, slug, admin, judge)


@router.get("/admin/problems/{slug}/hints")
def admin_hints(
    slug: str,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> list[AdminHintOut]:
    return service.admin_hints(db, slug)


@router.post("/admin/problems/{slug}/hints/{locale}/review")
def review_locale_hints(
    slug: str,
    locale: Locale,
    data: HintReview,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    judge: JudgeClient = Depends(get_judge),
) -> ProblemDetail:
    service.review_locale_hints(db, slug, locale, data, admin, client_ip(request))
    db.commit()  # one commit: every hint of the locale is reviewed and audited, or none
    return _admin_detail(db, slug, admin, judge)


@router.post("/admin/problems/{slug}/hints/{locale}/{ordinal}/review")
def review_hint(
    slug: str,
    locale: Locale,
    data: HintReview,
    request: Request,
    ordinal: int = Path(ge=1),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    judge: JudgeClient = Depends(get_judge),
) -> ProblemDetail:
    service.review_hint(db, slug, locale, ordinal, data, admin, client_ip(request))
    db.commit()
    return _admin_detail(db, slug, admin, judge)


@router.post("/admin/problems/{slug}/publish")
def publish(
    slug: str,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    judge: JudgeClient = Depends(get_judge),
) -> ProblemDetail:
    service.publish(db, slug, admin, client_ip(request))
    db.commit()
    return _admin_detail(db, slug, admin, judge)


@router.post("/admin/problems/{slug}/unpublish")
def unpublish(
    slug: str,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    judge: JudgeClient = Depends(get_judge),
) -> ProblemDetail:
    service.unpublish(db, slug, admin, client_ip(request))
    db.commit()
    return _admin_detail(db, slug, admin, judge)
