from datetime import datetime
from typing import Annotated, Literal

from pydantic import EmailStr, Field

from app.core.schemas import RequestModel, ResponseModel
from app.modules.users.models import Locale
from app.modules.users.schemas import DisplayName, UserPrivate

# Passwords are never stripped: leading/trailing spaces are significant.
Password = Annotated[str, Field(min_length=10, max_length=128)]


class RegisterRequest(RequestModel):
    email: EmailStr
    password: Password
    display_name: DisplayName
    preferred_locale: Locale = Locale.ru
    accepted_terms: Literal[True]


class LoginRequest(RequestModel):
    email: EmailStr
    password: Annotated[str, Field(min_length=1, max_length=128)]


class ChangePasswordRequest(RequestModel):
    current_password: Annotated[str, Field(min_length=1, max_length=128)]
    new_password: Password


class ResetPasswordRequest(RequestModel):
    token: Annotated[str, Field(min_length=1, max_length=128)]
    new_password: Password


class SessionInfo(ResponseModel):
    user: UserPrivate
    csrf_token: str
    expires_at: datetime
