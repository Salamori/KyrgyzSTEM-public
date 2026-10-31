"""Model providers behind one interface. Credentials are read from settings (env) only."""

from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Protocol

import anthropic
from anthropic.types.beta import BetaMessageParam, BetaOutputConfigParam

from app.core.config import get_settings

ANTHROPIC_DEFAULT_MODEL = "claude-opus-5-5"


@dataclass(frozen=True)
class ModelRequest:
    system: str
    user: str
    json_schema: dict[str, Any]


@dataclass(frozen=True)
class ModelResult:
    text: str
    model_id: str
    input_tokens: int
    output_tokens: int


class ProviderError(Exception):
    """kind: timeout | unavailable | rate_limited | auth | bad_request | refusal | truncated."""

    def __init__(self, kind: str, detail: str = "") -> None:
        super().__init__(f"{kind}: {detail}" if detail else kind)
        self.kind = kind


class AssistantModel(Protocol):
    @property
    def provider(self) -> str: ...

    @property
    def model_id(self) -> str: ...

    def generate(self, request: ModelRequest) -> ModelResult: ...


class AnthropicProvider:
    """Claude via the official SDK, with structured JSON output and refusal fallback."""

    provider = "anthropic"

    def __init__(
        self, api_key: str, model: str, effort: str, timeout: float, max_tokens: int
    ) -> None:
        self.model_id = model or ANTHROPIC_DEFAULT_MODEL
        self._effort = effort
        self._max_tokens = max_tokens
        self._client = anthropic.Anthropic(api_key=api_key, timeout=timeout, max_retries=1)

    def generate(self, request: ModelRequest) -> ModelResult:
        messages: list[BetaMessageParam] = [{"role": "user", "content": request.user}]
        output_config: BetaOutputConfigParam = {
            "effort": self._effort,  # type: ignore[typeddict-item]
            "format": {"type": "json_schema", "schema": request.json_schema},
        }
        try:
            response = self._client.beta.messages.create(
                model=self.model_id,
                max_tokens=self._max_tokens,
                system=request.system,
                messages=messages,
                output_config=output_config,
                # On a safety decline, re-run server-side on Anthropic's recommended model.
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except anthropic.APITimeoutError as exc:
            raise ProviderError("timeout") from exc
        except anthropic.RateLimitError as exc:
            raise ProviderError("rate_limited") from exc
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as exc:
            raise ProviderError("auth") from exc
        except anthropic.BadRequestError as exc:
            raise ProviderError("bad_request", exc.message) from exc
        except anthropic.APIStatusError as exc:
            raise ProviderError("unavailable", str(exc.status_code)) from exc
        except anthropic.APIConnectionError as exc:
            raise ProviderError("unavailable", "connection") from exc

        if response.stop_reason == "refusal":
            raise ProviderError("refusal")
        if response.stop_reason == "max_tokens":
            raise ProviderError("truncated")
        text = next((b.text for b in response.content if b.type == "text"), "")
        return ModelResult(
            text=text,
            model_id=response.model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )


@lru_cache
def _build(
    provider: str, api_key: str, model: str, effort: str, timeout: float, max_tokens: int
) -> AssistantModel | None:
    if provider == "anthropic" and api_key:
        return AnthropicProvider(api_key, model, effort, timeout, max_tokens)
    return None


def get_assistant_model() -> AssistantModel | None:
    """FastAPI dependency. `None` means the assistant is off (fallback only)."""
    s = get_settings()
    return _build(
        s.llm_provider.strip().lower(),
        s.llm_api_key,
        s.llm_model.strip(),
        s.llm_effort,
        s.llm_timeout_seconds,
        s.llm_max_tokens,
    )
