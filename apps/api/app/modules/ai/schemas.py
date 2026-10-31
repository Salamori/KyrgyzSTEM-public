import uuid
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints

from app.core.schemas import RequestModel
from app.modules.olympiad.schemas import HintOut
from app.modules.users.models import Locale

LEVEL_NAMES = {1: "nudge", 2: "direction", 3: "pseudocode", 4: "explanation"}


class AssistantRequest(RequestModel):
    level: Annotated[int, Field(ge=1, le=4)]
    question: Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)] | None = None
    submission_id: uuid.UUID | None = None


class SourceRef(BaseModel):
    id: str
    kind: str
    label: str


class GroundedPoint(BaseModel):
    text: str
    sources: list[SourceRef]


class AssistantAnswer(BaseModel):
    mode: Literal["ai", "fallback"]
    outcome: str
    level: int
    language: Locale
    language_note: str | None
    grounded: list[GroundedPoint]
    suggestion: str | None
    insufficient_sources: bool
    fallback_hints: list[HintOut]
    flags: list[str]
    model_id: str | None
    prompt_version: str
    used_today: int
    daily_limit: int


class AssistantStatus(BaseModel):
    enabled: bool
    model_id: str | None
    prompt_version: str
    daily_limit: int
    used_today: int
    reliable_locales: list[Locale]
