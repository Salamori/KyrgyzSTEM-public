import time
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import utcnow
from app.core.errors import ApiError
from app.modules.ai import prompting, retrieval, validation
from app.modules.ai.models import AiRequest
from app.modules.ai.provider import AssistantModel, ModelRequest, ProviderError
from app.modules.ai.schemas import (
    AssistantAnswer,
    AssistantRequest,
    AssistantStatus,
    GroundedPoint,
    SourceRef,
)
from app.modules.olympiad import service as olympiad
from app.modules.olympiad.models import Problem, ProblemStatus
from app.modules.olympiad.schemas import HintOut
from app.modules.users.models import Locale, User

FALLBACK_LOCALE = Locale.ru


def reliable_locales() -> list[Locale]:
    raw = get_settings().llm_reliable_locales
    return [Locale(x.strip()) for x in raw.split(",") if x.strip() in Locale.__members__]


def _day_start(now: datetime) -> datetime:
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


# Outcomes recorded without calling the provider. Every other request was a paid call
# (withheld answers and provider failures included), so it counts against quotas.
_NOT_CALLED = ("not_configured", "quota_exceeded", "budget_exhausted")


def used_today(db: Session, user: User, now: datetime) -> int:
    return (
        db.scalar(
            select(func.count()).where(
                AiRequest.user_id == user.id,
                AiRequest.outcome.not_in(_NOT_CALLED),
                AiRequest.created_at >= _day_start(now),
            )
        )
        or 0
    )


def ai_help_count(db: Session, user_id: uuid.UUID, problem_id: uuid.UUID) -> int:
    """Registered as an Olympiad help counter: AI answers count as help."""
    return (
        db.scalar(
            select(func.count()).where(
                AiRequest.user_id == user_id,
                AiRequest.problem_id == problem_id,
                AiRequest.mode == "ai",
                AiRequest.outcome == "ok",
            )
        )
        or 0
    )


def _budget_blocked(db: Session, now: datetime) -> bool:
    s = get_settings()
    today = (
        db.scalar(
            select(func.count()).where(
                AiRequest.outcome.not_in(_NOT_CALLED), AiRequest.created_at >= _day_start(now)
            )
        )
        or 0
    )
    month_start = _day_start(now).replace(day=1)
    spent = db.scalar(
        select(func.coalesce(func.sum(AiRequest.cost_usd), 0)).where(
            AiRequest.created_at >= month_start
        )
    ) or Decimal(0)
    return today >= s.ai_global_daily_cap or float(spent) >= s.ai_monthly_budget_usd


def status(db: Session, user: User, model: AssistantModel | None) -> AssistantStatus:
    s = get_settings()
    return AssistantStatus(
        enabled=model is not None,
        model_id=model.model_id if model else None,
        prompt_version=prompting.PROMPT_VERSION,
        daily_limit=s.ai_daily_quota_per_user,
        used_today=used_today(db, user, utcnow()),
        reliable_locales=reliable_locales(),
    )


def _cost(input_tokens: int, output_tokens: int) -> float:
    s = get_settings()
    return (
        input_tokens * s.llm_price_input_per_mtok + output_tokens * s.llm_price_output_per_mtok
    ) / 1e6


def ask(
    db: Session,
    user: User,
    slug: str,
    data: AssistantRequest,
    locale: Locale,
    model: AssistantModel | None,
) -> AssistantAnswer:
    problem = olympiad.get_problem(db, slug, user)
    if problem.status != ProblemStatus.published:
        # Only reviewed, published material is "approved" for grounding.
        raise ApiError(404, "not_found", "The assistant is available for published problems only.")
    unlocked = olympiad.editorial_unlocked(db, user, problem)
    if data.level == 4 and not unlocked:
        raise ApiError(
            403,
            "assistant_level_locked",
            "The detailed explanation opens after you solve or give up.",
        )
    student_code = None
    if data.submission_id is not None:
        submission = olympiad.get_submission(db, data.submission_id, user)
        if submission.user_id != user.id or submission.problem_id != problem.id:
            raise ApiError(404, "not_found", "Submission not found.")
        student_code = (submission.language.value, submission.source_code)

    s = get_settings()
    now = utcnow()
    answered = locale if locale in reliable_locales() else FALLBACK_LOCALE
    language_note = None if answered == locale else "locale_not_reliable"
    record = AiRequest(
        user_id=user.id,
        problem_id=problem.id,
        level=data.level,
        locale_requested=locale.value,
        locale_answered=answered.value,
        prompt_version=prompting.PROMPT_VERSION,
        provider=model.provider if model else None,
        model_id=model.model_id if model else None,
    )

    def fallback(outcome: str, flags: list[str] | None = None) -> AssistantAnswer:
        record.mode, record.outcome, record.flags = "fallback", outcome, flags or []
        db.add(record)
        revealed = _reveal_authored(db, problem, user, data.level, locale)
        return AssistantAnswer(
            mode="fallback",
            outcome=outcome,
            level=data.level,
            language=locale,
            language_note=None,
            grounded=[],
            suggestion=None,
            insufficient_sources=not revealed,
            fallback_hints=revealed,
            flags=flags or [],
            model_id=None,
            prompt_version=prompting.PROMPT_VERSION,
            used_today=used_today(db, user, now),
            daily_limit=s.ai_daily_quota_per_user,
        )

    if model is None:
        return fallback("not_configured")
    if used_today(db, user, now) >= s.ai_daily_quota_per_user:
        return fallback("quota_exceeded")
    if _budget_blocked(db, now):
        return fallback("budget_exhausted")

    sources, editorial = retrieval.collect_sources(db, problem, answered, include_editorial=True)
    system, user_message = prompting.build(
        data.level, answered.value, sources, data.question, student_code
    )
    started = time.monotonic()
    try:
        result = model.generate(ModelRequest(system, user_message, prompting.OUTPUT_SCHEMA))
    except ProviderError as exc:
        record.latency_ms = int((time.monotonic() - started) * 1000)
        return fallback(f"provider_{exc.kind}")
    record.latency_ms = int((time.monotonic() - started) * 1000)
    record.model_id = result.model_id
    record.input_tokens, record.output_tokens = result.input_tokens, result.output_tokens
    record.cost_usd = _cost(result.input_tokens, result.output_tokens)

    checked = validation.validate(result.text, data.level, sources, editorial)
    if checked.withheld:
        return fallback(checked.withheld, [checked.withheld, *checked.flags])

    record.mode, record.outcome, record.flags = "ai", "ok", checked.flags
    db.add(record)
    db.flush()
    by_id = {src.id: src for src in sources}
    return AssistantAnswer(
        mode="ai",
        outcome="ok",
        level=data.level,
        language=answered,
        language_note=language_note,
        grounded=[
            GroundedPoint(
                text=text,
                sources=[SourceRef(id=i, kind=by_id[i].kind, label=by_id[i].label) for i in ids],
            )
            for text, ids in checked.grounded
        ],
        suggestion=checked.suggestion or None,
        insufficient_sources=checked.insufficient_sources,
        fallback_hints=[],
        flags=checked.flags,
        model_id=result.model_id,
        prompt_version=prompting.PROMPT_VERSION,
        used_today=used_today(db, user, now),
        daily_limit=s.ai_daily_quota_per_user,
    )


def _reveal_authored(
    db: Session, problem: Problem, user: User, level: int, locale: Locale
) -> list[HintOut]:
    """Deterministic fallback: authored hints 1..min(level, n), recorded as normal reveals."""
    current = olympiad.hints(db, problem, user, locale)
    target = min(level, current.total)
    for ordinal in range(len(current.revealed) + 1, target + 1):
        current = olympiad.reveal_hint(db, problem, user, ordinal, locale)
    return list(current.revealed)
