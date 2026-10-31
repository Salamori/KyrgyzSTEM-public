from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator


class RequestModel(BaseModel):
    """Base for request bodies: unknown fields are rejected (prevents mass assignment)."""

    model_config = ConfigDict(extra="forbid")

    @field_validator("*", mode="after")
    @classmethod
    def _no_nul(cls, value: Any) -> Any:
        # PostgreSQL text cannot hold NUL; reject it here instead of failing in the database.
        if isinstance(value, str) and "\x00" in value:
            raise ValueError("must not contain NUL characters")
        return value


class ResponseModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Page[T](BaseModel):
    items: list[T]
    total: int
    limit: int
    offset: int


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: object | None = None


class ErrorResponse(BaseModel):
    error: ErrorDetail
