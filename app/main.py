from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from secrets import token_urlsafe

from asyncpg import PostgresError
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from sqlalchemy.exc import SQLAlchemyError
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException

from app.api import auth, documents, health, users
from app.api.auth_body import RequestMiddleware
from app.api.error_handlers import (
    app_error_handler,
    database_error_handler,
    http_error_handler,
    unexpected_error_handler,
    validation_error_handler,
)
from app.config import Settings
from app.errors import AppError
from app.logging_config import configure_logging
from app.schemas.responses.common import ApiResponse, ErrorData
from app.services.auth import hash_password
from app.storage.session import create_engine, create_session_factory


def create_app(settings: Settings | None = None) -> FastAPI:
    configure_logging()
    settings = settings if settings is not None else Settings()
    if settings.jwt_secret is None:
        raise ValueError("JWT_SECRET is required to start the API")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_engine(settings)
        app.state.engine = engine
        app.state.session_factory = create_session_factory(engine)
        try:
            app.state.dummy_password_hash = await run_in_threadpool(
                hash_password, token_urlsafe(32)
            )
            yield
        finally:
            await engine.dispose()

    app = FastAPI(
        title="Chunking PDF",
        version="0.1.0",
        lifespan=lifespan,
        responses={
            code: {"model": ApiResponse[ErrorData]}
            for code in (400, 401, 403, 404, 408, 409, 410, 413, 415, 422, 429, 500, 503)
        },
    )
    app.state.settings = settings
    app.add_middleware(RequestMiddleware)
    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(HTTPException, http_error_handler)
    for error_type in (SQLAlchemyError, PostgresError, OSError, TimeoutError):
        app.add_exception_handler(error_type, database_error_handler)
    app.add_exception_handler(Exception, unexpected_error_handler)
    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(users.router)
    app.include_router(documents.router)
    return app
