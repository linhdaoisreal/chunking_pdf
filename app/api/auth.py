from fastapi import APIRouter, Request
from starlette.concurrency import run_in_threadpool

from app.api.dependencies import CurrentUser
from app.errors import AppError
from app.schemas.requests.auth import LoginRequest, RegisterRequest
from app.schemas.responses.auth import LogoutResponse, TokenResponse
from app.schemas.responses.common import ApiResponse
from app.schemas.responses.users import UserResponse
from app.services.auth import create_access_token, hash_password, verify_password
from app.storage import users
from app.storage.rate_limits import check_rate_limit

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


async def limit_identity(request: Request, email: str) -> None:
    settings = request.app.state.settings
    await check_rate_limit(
        request.app.state.session_factory,
        "email",
        email,
        settings.auth_identity_limit,
        settings.auth_rate_window_seconds,
    )


@router.post("/register", status_code=201, response_model=ApiResponse[UserResponse])
async def register(payload: RegisterRequest, request: Request) -> ApiResponse[UserResponse]:
    await limit_identity(request, payload.email)
    password_hash = await run_in_threadpool(hash_password, payload.password.get_secret_value())
    user = await users.create_user(
        request.app.state.session_factory, payload.email, password_hash, payload.display_name
    )
    return ApiResponse(statusCode=201, data=UserResponse.model_validate(user))


@router.post("/login", response_model=ApiResponse[TokenResponse])
async def login(payload: LoginRequest, request: Request) -> ApiResponse[TokenResponse]:
    await limit_identity(request, payload.email)
    user = await users.get_by_email(request.app.state.session_factory, payload.email)
    verified = await run_in_threadpool(
        verify_password,
        payload.password.get_secret_value(),
        user.password_hash if user else request.app.state.dummy_password_hash,
    )
    if not verified or user is None:
        raise AppError(
            401, "invalid_credentials", "Invalid email or password", {"WWW-Authenticate": "Bearer"}
        )
    if not user.is_active:
        raise AppError(403, "inactive_user", "Account is inactive")
    settings = request.app.state.settings
    return ApiResponse(
        statusCode=200,
        data=TokenResponse(
            access_token=create_access_token(user.id, settings),
            expires_in=settings.jwt_access_ttl_seconds,
        ),
    )


@router.post("/logout", response_model=ApiResponse[LogoutResponse])
async def logout(user: CurrentUser) -> ApiResponse[LogoutResponse]:
    return ApiResponse(statusCode=200, data=LogoutResponse())
