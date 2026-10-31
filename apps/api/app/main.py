import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Literal

from fastapi import APIRouter, Depends, FastAPI, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_db
from app.core.errors import ApiError, register_error_handlers
from app.core.logs import REQUEST_ID_PATTERN, configure_logging, request_id_var
from app.core.schemas import ErrorResponse
from app.modules.ai.router import router as ai_router
from app.modules.ai.service import ai_help_count
from app.modules.auth.router import router as auth_router
from app.modules.judge.base import get_judge
from app.modules.olympiad.help import register_help_counter
from app.modules.olympiad.router import router as olympiad_router
from app.modules.orgs.router import router as orgs_router
from app.modules.users.router import router as users_router

API_PREFIX = "/api/v1"

logger = logging.getLogger("kyrgyzstem.request")

_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Cache-Control": "no-store",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
}


class Health(BaseModel):
    status: Literal["ok"]
    database: Literal["ok", "unavailable"]


health_router = APIRouter(tags=["meta"])


def _database_ok(db: Session) -> bool:
    try:
        db.execute(text("SELECT 1"))
        return True
    except SQLAlchemyError:
        return False


@health_router.get("/health")
def health(db: Session = Depends(get_db)) -> Health:
    """Liveness: the process answers. Always 200; reports the database state."""
    return Health(status="ok", database="ok" if _database_ok(db) else "unavailable")


@health_router.get("/health/ready", responses={503: {"model": ErrorResponse}})
def ready(db: Session = Depends(get_db)) -> Health:
    """Readiness: 200 only when the database answers, else 503 service_unavailable.

    Optional dependencies (judge, AI provider) never make the API unready.
    """
    if not _database_ok(db):
        raise ApiError(
            503,
            "service_unavailable",
            "The database is unavailable.",
            headers={"Retry-After": "30"},
        )
    return Health(status="ok", database="ok")


def create_app() -> FastAPI:
    settings = get_settings()
    is_prod = settings.app_env == "production"
    app = FastAPI(
        title="KyrgyzSTEM API",
        version="0.1.0",
        openapi_url=None if is_prod else f"{API_PREFIX}/openapi.json",
        docs_url=None if is_prod else f"{API_PREFIX}/docs",
        redoc_url=None,
        responses={
            400: {"model": ErrorResponse},
            401: {"model": ErrorResponse},
            403: {"model": ErrorResponse},
            404: {"model": ErrorResponse},
            409: {"model": ErrorResponse},
            422: {"model": ErrorResponse},
            429: {"model": ErrorResponse},
        },
    )
    register_error_handlers(app)
    if settings.app_env != "test":  # tests keep pytest's own log capture
        configure_logging(settings.log_level, settings.log_format)

    @app.middleware("http")
    async def request_log(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        incoming = request.headers.get("x-request-id", "")
        request_id = incoming if REQUEST_ID_PATTERN.match(incoming) else uuid.uuid4().hex
        token = request_id_var.set(request_id)
        started = time.perf_counter()
        try:
            try:
                response = await call_next(request)
            except Exception:
                # Traceback only: no request body, headers or query string.
                logger.exception("request.unhandled_error")
                response = JSONResponse(
                    status_code=500,
                    content={
                        "error": {
                            "code": "internal_error",
                            "message": "Something went wrong on our side.",
                            "details": {"request_id": request_id},
                        }
                    },
                )
            # The route template (e.g. /api/v1/problems/{slug}), never the raw path. FastAPI
            # keeps an included router's prefix outside the matched route, so add it back.
            matched = request.scope.get("route")
            route = str(getattr(matched, "path", "unmatched"))
            if matched is not None and not route.startswith(API_PREFIX):
                route = API_PREFIX + route
            fields = {
                "method": request.method,
                "route": route,
                "status": response.status_code,
                "duration_ms": round((time.perf_counter() - started) * 1000, 1),
            }
            is_probe = route.startswith(f"{API_PREFIX}/health")
            logger.log(
                logging.DEBUG if is_probe else logging.INFO, "request", extra={"fields": fields}
            )
            response.headers["X-Request-ID"] = request_id
            return response
        finally:
            request_id_var.reset(token)

    # Registered last, so it runs outermost: every response gets these headers, including
    # the 500s that request_log generates for unhandled errors.
    @app.middleware("http")
    async def security_headers(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        if not request.url.path.startswith(f"{API_PREFIX}/docs"):
            for name, value in _SECURITY_HEADERS.items():
                response.headers.setdefault(name, value)
        return response

    # AI answers count as help on later submissions (olympiad never imports ai).
    register_help_counter(ai_help_count)
    for router in (
        health_router,
        auth_router,
        users_router,
        olympiad_router,
        ai_router,
        orgs_router,
    ):
        app.include_router(router, prefix=API_PREFIX)
    logging.getLogger("kyrgyzstem").info(
        "api.configured",
        extra={
            "fields": {
                "app_env": settings.app_env,
                "judge": "available" if get_judge().available else "not_configured",
                "assistant": "on" if settings.llm_provider and settings.llm_api_key else "off",
            }
        },
    )
    return app


app = create_app()
