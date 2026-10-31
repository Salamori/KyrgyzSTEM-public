import re
from functools import lru_cache
from pathlib import Path, PurePath
from typing import Literal, Self
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Where content/ lives when CONTENT_DIR is not set. Docker images mount it at this path
# (docker-compose.yml, deploy/docker-compose.prod.yml) and set CONTENT_DIR to it.
CONTAINER_CONTENT_DIR = Path("/content")

_LIMITER_SECRET = re.compile(r"[A-Za-z0-9_-]{32,}")


def default_content_dir(module_file: PurePath | None = None) -> PurePath:
    """The repository's content/ in a checkout, else the container mount point.

    In a checkout this module is <repo>/apps/api/app/core/config.py, so content/ is four
    levels up. In the API image the build context is apps/api only (WORKDIR /app), so the
    module is /app/app/core/config.py and there is no repository root to walk up to.
    """
    here = module_file if module_file is not None else Path(__file__).resolve()
    parents = here.parents
    in_checkout = (
        len(parents) > 4
        and parents[0].name == "core"
        and parents[1].name == "app"
        and parents[2].name == "api"
        and parents[3].name == "apps"
    )
    return parents[4] / "content" if in_checkout else CONTAINER_CONTENT_DIR


class Settings(BaseSettings):
    # hide_input_in_errors: a startup error must not print settings values (secrets) to logs.
    model_config = SettingsConfigDict(
        env_file=(".env", "../../.env"), extra="ignore", hide_input_in_errors=True
    )

    app_env: Literal["development", "test", "production"] = "development"
    database_url: str = "postgresql+psycopg://kyrgyzstem:kyrgyzstem@localhost:5432/kyrgyzstem"

    session_cookie_name: str = "ks_session"
    csrf_cookie_name: str = "ks_csrf"
    csrf_header_name: str = "X-CSRF-Token"
    session_cookie_secure: bool = False
    session_ttl_hours: int = 24 * 14
    # A session unused for this long ends, whatever its absolute expiry (audit F-16).
    session_idle_hours: int = Field(default=24 * 7, ge=1)
    password_reset_ttl_minutes: int = Field(default=60, ge=5, le=1440)

    # Version of the legal documents new accounts accept; change it with the documents.
    legal_documents_version: str = Field(default="draft-2026-10-31", max_length=32)

    # Repository content (curriculum + problems); /content inside Docker (CONTENT_DIR).
    content_dir: Path = Field(default_factory=lambda: Path(default_content_dir()))

    # Development seed only (scripts/seed.py refuses to run in production).
    seed_password: str = ""

    # Optional AI assistant (docs/proposals/0004). Off unless provider and key are set.
    llm_provider: str = ""  # "" (off) | "anthropic"
    llm_api_key: str = ""
    llm_model: str = ""  # empty → provider default
    llm_effort: Literal["low", "medium", "high"] = "medium"
    llm_timeout_seconds: float = 30.0
    llm_max_tokens: int = 16000
    llm_reliable_locales: str = "en,ru"  # add "ky" only after native-speaker evaluation
    llm_price_input_per_mtok: float = 4.0  # USD, for cost estimates only
    llm_price_output_per_mtok: float = 20.0
    ai_daily_quota_per_user: int = 20
    ai_global_daily_cap: int = 1000
    ai_monthly_budget_usd: float = 25.0

    # School/club operations (docs/SCHOOL_OPERATIONS.md).
    attendance_retention_days: int = 365
    join_code_ttl_days: int = 7

    # Judge queue (judge/queue.py). Out-of-range values stop the process at startup.
    judge_lease_seconds: int = Field(default=120, ge=30, le=600)
    judge_renew_seconds: int = Field(default=30, ge=5, le=300)  # at most half the lease
    judge_max_attempts: int = Field(default=3, ge=1, le=10)
    judge_poll_seconds: float = Field(default=2.0, ge=0.5, le=60)
    # No new test of a submission starts after this (the job is released as judge_timeout).
    judge_max_judging_seconds: int = Field(default=1800, ge=60, le=21600)
    # Persistent worker (judge/runner.py, scripts/judge_worker.py).
    judge_backoff_max_seconds: float = Field(default=60.0, ge=1, le=600)
    judge_shutdown_grace_seconds: float = Field(default=30.0, ge=0, le=600)
    judge_worker_health_file: str = ""  # empty: no health file
    # Languages the API queues for judging; empty (default): nothing is queued, every
    # submission is stored as not_judged. The API never contacts Judge0 itself: set this
    # only where a worker with these languages configured is running.
    judge_languages: str = ""

    # Judge0 CE executor (judge/judge0.py, Step 5.4.1). Empty URL: not configured. Not wired
    # into the API or the worker yet. Language ids have no defaults on purpose: they depend
    # on the Judge0 installation and must be read from its /languages and set explicitly.
    judge0_url: str = ""
    judge0_auth_token: SecretStr = SecretStr("")  # sent as X-Auth-Token; never logged
    judge0_timeout_seconds: float = Field(default=10.0, ge=1, le=120)  # per HTTP request
    judge0_poll_seconds: float = Field(default=1.0, ge=0.2, le=30)
    judge0_max_wait_seconds: float = Field(default=300.0, ge=10, le=3600)  # per test
    judge0_language_id_cpp: int | None = Field(default=None, ge=1)
    judge0_language_id_python: int | None = Field(default=None, ge=1)
    judge0_time_factor_cpp: float = Field(default=1.0, ge=1, le=10)
    judge0_time_factor_python: float = Field(default=1.0, ge=1, le=10)
    judge0_memory_factor_cpp: float = Field(default=1.0, ge=1, le=10)
    judge0_memory_factor_python: float = Field(default=1.0, ge=1, le=10)
    judge0_wall_time_factor: float = Field(default=3.0, ge=1, le=10)  # wall = CPU limit x this
    judge0_max_output_bytes: int = Field(default=1 << 20, ge=1024, le=64 << 20)  # per test
    judge0_max_response_bytes: int = Field(default=16 << 20, ge=1 << 16, le=256 << 20)

    # Rate limits can be disabled only in tests that are not about rate limiting.
    rate_limit_enabled: bool = True
    # Where limiter state lives (core/rate_limit.py). "memory": this process only, so one
    # worker (docker-compose). "postgres": shared by every instance; required on Vercel.
    rate_limit_backend: Literal["memory", "postgres"] = "memory"
    # HMAC key for the limiter keys stored by the postgres backend, so the table never holds
    # IP addresses or emails. 32+ letters, digits, '-' or '_'.
    rate_limit_key_secret: SecretStr = SecretStr("")
    # The postgres backend's own pool, separate from the request pool: an authenticated
    # request already holds a request connection when it is rate-limited, so sharing one
    # pool could leave every request holding one connection and waiting for another.
    # Fixed size (no overflow); a hit that cannot get a connection in time fails with 503.
    rate_limit_pool_size: int = Field(default=2, ge=1, le=10)
    rate_limit_pool_timeout_seconds: float = Field(default=5.0, ge=0.5, le=30)

    # Client address (modules/auth/deps.py). "socket": the ASGI client, which uvicorn takes
    # from X-Forwarded-For only for --forwarded-allow-ips (audit F-01). "vercel": the
    # x-forwarded-for that Vercel's edge writes; allowed only on Vercel.
    client_ip_source: Literal["socket", "vercel"] = "socket"
    # "1" on Vercel (set by the platform at build and run time); never set it elsewhere.
    vercel: str = ""

    # Connection pool of each process. Serverless instances each hold one: keep it small
    # there. Behind a transaction-mode pooler (PgBouncer) prepared statements may need to be
    # turned off.
    database_pool_size: int = Field(default=5, ge=1, le=20)
    database_max_overflow: int = Field(default=10, ge=0, le=50)
    database_prepared_statements: bool = True

    # Structured logs on stderr (app/core/logs.py). "text" is for local reading.
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_format: Literal["json", "text"] = "json"

    @field_validator("judge0_language_id_cpp", "judge0_language_id_python", mode="before")
    @classmethod
    def _empty_language_id_is_unset(cls, value: object) -> object:
        # `JUDGE0_LANGUAGE_ID_CPP=` (as in .env.example and compose defaults) means unset.
        return None if isinstance(value, str) and not value.strip() else value

    @model_validator(mode="after")
    def _production_is_https(self) -> Self:
        if self.app_env == "production" and not self.session_cookie_secure:
            raise ValueError("APP_ENV=production requires SESSION_COOKIE_SECURE=true")
        return self

    @model_validator(mode="after")
    def _vercel_needs_shared_limits_and_its_client_ip(self) -> Self:
        on_vercel = self.vercel == "1"
        # Trusting x-forwarded-for anywhere but behind Vercel's edge lets a client pick its
        # rate-limit identity (audit F-01).
        if self.client_ip_source == "vercel" and not on_vercel:
            raise ValueError("CLIENT_IP_SOURCE=vercel is allowed only on Vercel (VERCEL=1)")
        if on_vercel and self.client_ip_source != "vercel":
            raise ValueError("On Vercel set CLIENT_IP_SOURCE=vercel")
        if on_vercel and self.rate_limit_backend != "postgres":
            raise ValueError(
                "On Vercel set RATE_LIMIT_BACKEND=postgres (instances share no memory)"
            )
        secret = self.rate_limit_key_secret.get_secret_value()
        if self.rate_limit_backend == "postgres" and not _LIMITER_SECRET.fullmatch(secret):
            raise ValueError(
                "RATE_LIMIT_BACKEND=postgres requires RATE_LIMIT_KEY_SECRET "
                "(32+ letters, digits, '-' or '_')"
            )
        return self

    @model_validator(mode="after")
    def _judge0_url_is_http(self) -> Self:
        if not self.judge0_url:
            return self
        if not self.judge0_url.startswith(("http://", "https://")):
            raise ValueError("JUDGE0_URL must be an http(s) URL")
        parts = urlsplit(self.judge0_url)
        # Credentials belong in JUDGE0_AUTH_TOKEN, never in a URL that may be logged.
        if not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
            raise ValueError("JUDGE0_URL must be a plain base URL (no credentials or query)")
        if self.app_env == "production" and not self.judge0_auth_token.get_secret_value():
            raise ValueError("APP_ENV=production requires JUDGE0_AUTH_TOKEN with JUDGE0_URL")
        return self

    @model_validator(mode="after")
    def _judge_worker_settings(self) -> Self:
        if self.judge_backoff_max_seconds < self.judge_poll_seconds:
            raise ValueError("JUDGE_BACKOFF_MAX_SECONDS must be at least JUDGE_POLL_SECONDS")
        self.judged_languages()  # validates JUDGE_LANGUAGES
        return self

    def judged_languages(self) -> frozenset[str]:
        """JUDGE_LANGUAGES as language values ("cpp", "python"); unknown names are rejected."""
        names = {n.strip() for n in self.judge_languages.split(",") if n.strip()}
        unknown = names - {"cpp", "python"}
        if unknown:
            raise ValueError(f"JUDGE_LANGUAGES has unknown languages: {sorted(unknown)}")
        return frozenset(names)

    @model_validator(mode="after")
    def _judge_renewal_fits_lease(self) -> Self:
        # Renewing at most every half lease leaves a full renewal interval of slack.
        if 2 * self.judge_renew_seconds > self.judge_lease_seconds:
            raise ValueError("JUDGE_RENEW_SECONDS must be at most half of JUDGE_LEASE_SECONDS")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
