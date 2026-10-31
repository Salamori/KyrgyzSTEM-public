"""Extension point: other modules report extra help a student received on a problem.

The AI module registers a counter in `main.py`, so `olympiad` never imports it. Without
registered counters (Phase 2 behaviour) extra help is always 0.
"""

import uuid
from collections.abc import Callable

from sqlalchemy.orm import Session

HelpCounter = Callable[[Session, uuid.UUID, uuid.UUID], int]

_counters: list[HelpCounter] = []


def register_help_counter(counter: HelpCounter) -> None:
    if counter not in _counters:
        _counters.append(counter)


def extra_help(db: Session, user_id: uuid.UUID, problem_id: uuid.UUID) -> int:
    return sum(counter(db, user_id, problem_id) for counter in _counters)
