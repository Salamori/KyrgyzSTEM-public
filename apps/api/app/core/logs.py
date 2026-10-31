"""Structured application logging.

One JSON object per line on stderr. Records carry only what operators need: event name,
method, route *template* (never the raw path or query string, which can hold emails or
slugs), status, duration and a request id. Never pass request bodies, cookies, tokens,
IP addresses, emails or user ids to a logger.
"""

import json
import logging
import re
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

# Incoming X-Request-ID values are reused only if they look like an id, so a client
# cannot inject arbitrary text into the logs.
REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

_HANDLER_MARK = "_kyrgyzstem_handler"


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "event": record.getMessage(),
        }
        request_id = request_id_var.get()
        if request_id:
            entry["request_id"] = request_id
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            entry.update(fields)
        if record.exc_info:
            entry["exc_type"] = record.exc_info[0].__name__ if record.exc_info[0] else None
            entry["traceback"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str, ensure_ascii=False)


class TextFormatter(logging.Formatter):
    """Human-readable variant for local development (LOG_FORMAT=text)."""

    def format(self, record: logging.LogRecord) -> str:
        line = f"{record.levelname:<7} {record.name}: {record.getMessage()}"
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict) and fields:
            line += " " + " ".join(f"{k}={v}" for k, v in fields.items())
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line


def configure_logging(level: str = "INFO", fmt: str = "json") -> None:
    """Route the app's and uvicorn's loggers through one formatter. Idempotent."""
    root = logging.getLogger()
    for handler in [h for h in root.handlers if getattr(h, _HANDLER_MARK, False)]:
        root.removeHandler(handler)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter() if fmt == "json" else TextFormatter())
    setattr(handler, _HANDLER_MARK, True)
    root.addHandler(handler)
    root.setLevel(level.upper())

    for name in ("uvicorn", "uvicorn.error"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True
    # uvicorn's access log records the client address and the full query string;
    # the request middleware in app.main logs a safer equivalent instead.
    logging.getLogger("uvicorn.access").disabled = True
