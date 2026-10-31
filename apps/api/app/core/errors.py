import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DataError, OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger("kyrgyzstem.errors")


class ApiError(Exception):
    """Raise from routes/services; rendered as {"error": {code, message, details}}."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: Any = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details
        self.headers = headers


def _body(code: str, message: str, details: Any = None) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": details}}


_STATUS_CODES = {
    400: "bad_request",
    401: "not_authenticated",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
}


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=_body(exc.code, exc.message, exc.details),
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        # Never echo submitted values back (they may contain passwords).
        details = [
            {"loc": list(e.get("loc", ())), "msg": e.get("msg", ""), "type": e.get("type", "")}
            for e in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content=jsonable_encoder(_body("validation_error", "Invalid request.", details)),
        )

    @app.exception_handler(DataError)
    async def _data_error(_: Request, exc: DataError) -> JSONResponse:
        # A value the database cannot store or compare (NUL in a query parameter, an
        # out-of-range offset): the client's fault, and never worth echoing back.
        return JSONResponse(
            status_code=400, content=_body("bad_request", "Invalid value in the request.")
        )

    @app.exception_handler(OperationalError)
    @app.exception_handler(PoolTimeoutError)
    async def _database_unavailable(_: Request, exc: Exception) -> JSONResponse:
        # Database down or overloaded: a clear, retryable error instead of a stack trace.
        # Only the class name is logged; the message may name the database host.
        logger.warning("database.unavailable", extra={"fields": {"exc_type": type(exc).__name__}})
        return JSONResponse(
            status_code=503,
            content=_body("service_unavailable", "The service is temporarily unavailable."),
            headers={"Retry-After": "30"},
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _STATUS_CODES.get(exc.status_code, "error")
        return JSONResponse(
            status_code=exc.status_code,
            content=_body(code, str(exc.detail)),
            headers=getattr(exc, "headers", None),
        )
