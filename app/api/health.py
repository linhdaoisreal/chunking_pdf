from asyncpg import PostgresError
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from app.schemas.responses.common import ApiResponse, StatusData
from app.storage.session import check_connection

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live", response_model=ApiResponse[StatusData])
async def live() -> ApiResponse[StatusData]:
    return ApiResponse(statusCode=200, data=StatusData(status="ok"))


@router.get(
    "/ready",
    response_model=ApiResponse[StatusData],
    responses={503: {"model": ApiResponse[StatusData]}},
)
async def ready(request: Request) -> JSONResponse:
    # shortcut: connectivity only; check migration head when the first schema is added.
    try:
        await check_connection(request.app.state.engine)
    except (SQLAlchemyError, PostgresError, OSError, TimeoutError):
        return JSONResponse({"statusCode": 503, "data": {"status": "unavailable"}}, status_code=503)
    return JSONResponse({"statusCode": 200, "data": {"status": "ready"}})
