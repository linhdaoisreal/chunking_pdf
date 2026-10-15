import logging
from http import HTTPStatus

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from app.errors import AppError
from app.schemas.responses.common import ApiResponse, ErrorData, ErrorDetail


def error_response(request: Request, error: AppError) -> JSONResponse:
    request_id = request.state.request_id
    if request.url.path.rstrip("/") == "/api/v1/documents/upload":
        logging.getLogger(__name__).warning(
            "Upload rejected: request=%s status=%s code=%s",
            request_id,
            error.status_code,
            error.code,
        )
    payload = ApiResponse(
        statusCode=error.status_code,
        data=ErrorData(
            error=ErrorDetail(code=error.code, message=error.message), request_id=request_id
        ),
    )
    return JSONResponse(
        payload.model_dump(),
        status_code=error.status_code,
        headers={"Cache-Control": "no-store", "X-Request-ID": request_id, **(error.headers or {})},
    )


async def app_error_handler(request: Request, error: AppError) -> JSONResponse:
    return error_response(request, error)


async def validation_error_handler(request: Request, error: RequestValidationError) -> JSONResponse:
    # Validation details can contain passwords and raw request bodies.
    return error_response(request, AppError(422, "invalid_request", "Invalid request"))


async def http_error_handler(request: Request, error: HTTPException) -> JSONResponse:
    return error_response(
        request,
        AppError(
            error.status_code, "http_error", HTTPStatus(error.status_code).phrase, error.headers
        ),
    )


async def database_error_handler(request: Request, error: Exception) -> JSONResponse:
    return error_response(request, AppError(503, "database_unavailable", "Database unavailable"))


async def unexpected_error_handler(request: Request, error: Exception) -> JSONResponse:
    return error_response(request, AppError(500, "internal_error", "Internal server error"))
