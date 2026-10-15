from fastapi import APIRouter

from app.api.dependencies import CurrentUser
from app.schemas.responses.common import ApiResponse
from app.schemas.responses.users import UserResponse

router = APIRouter(prefix="/api/v1/users", tags=["users"])


@router.get("/me", response_model=ApiResponse[UserResponse])
async def me(user: CurrentUser) -> ApiResponse[UserResponse]:
    return ApiResponse(statusCode=200, data=UserResponse.model_validate(user))
