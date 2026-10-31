from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.rate_limit import limiter
from app.modules.ai import service
from app.modules.ai.provider import AssistantModel, get_assistant_model
from app.modules.ai.schemas import AssistantAnswer, AssistantRequest, AssistantStatus
from app.modules.auth.deps import require_user
from app.modules.olympiad.service import requested_locale
from app.modules.users.models import Locale, User

router = APIRouter(tags=["assistant"])


@router.get("/assistant/status")
def assistant_status(
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
    model: AssistantModel | None = Depends(get_assistant_model),
) -> AssistantStatus:
    return service.status(db, user, model)


@router.post("/problems/{slug}/assistant")
def ask_assistant(
    slug: str,
    data: AssistantRequest,
    locale: Locale | None = None,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
    model: AssistantModel | None = Depends(get_assistant_model),
) -> AssistantAnswer:
    limiter.hit(f"assistant:{user.id}", limit=6, window_seconds=60)
    answer = service.ask(db, user, slug, data, requested_locale(locale, user), model)
    db.commit()
    return answer
