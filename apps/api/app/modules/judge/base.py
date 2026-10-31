"""Judge interface, as the API sees it. The API never executes submitted code itself and
never contacts Judge0: it only decides whether a new submission is queued.

`JUDGE_LANGUAGES` empty (the default): `NoJudge`, every submission is stored as `not_judged`
and the platform keeps working. Set: `QueueJudge`, and submissions in those languages are
queued for the judge worker (judge/runner.py, a separate process that talks to Judge0).
"""

from typing import Protocol

from app.core.config import get_settings


class JudgeClient(Protocol):
    @property
    def available(self) -> bool: ...

    def accepts(self, language: str) -> bool: ...


class NoJudge:
    @property
    def available(self) -> bool:
        return False

    def accepts(self, language: str) -> bool:
        return False


class QueueJudge:
    """Judging by the worker, for the configured languages only."""

    def __init__(self, languages: frozenset[str]) -> None:
        self._languages = languages

    @property
    def available(self) -> bool:
        return bool(self._languages)

    def accepts(self, language: str) -> bool:
        return language in self._languages


def get_judge() -> JudgeClient:
    languages = get_settings().judged_languages()
    return QueueJudge(languages) if languages else NoJudge()
