from typing import Annotated

import jwt
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.errors import AppError
from app.services.auth import decode_access_token
from app.storage import users
from app.storage.models import User

bearer = HTTPBearer(auto_error=False, description="JWT access token from /api/v1/auth/login")


async def current_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> User:
    unauthorized = AppError(
        401, "unauthorized", "Invalid or expired access token", {"WWW-Authenticate": "Bearer"}
    )
    if credentials is None:
        raise unauthorized
    try:
        user_id = decode_access_token(credentials.credentials, request.app.state.settings)
    except jwt.InvalidTokenError:
        raise unauthorized from None
    user = await users.get_by_id(request.app.state.session_factory, user_id)
    if user is None:
        raise unauthorized
    if not user.is_active:
        raise AppError(403, "inactive_user", "Account is inactive")
    return user


CurrentUser = Annotated[User, Depends(current_user)]
