import logging
import time
from uuid import uuid4

from asyncpg import PostgresError
from fastapi import Request
from sqlalchemy.exc import SQLAlchemyError
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.api.error_handlers import database_error_handler, error_response
from app.errors import AppError
from app.storage.rate_limits import check_rate_limit

AUTH_BODY_LIMIT = 16 * 1024


class RequestMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = str(uuid4())
        scope.setdefault("state", {})["request_id"] = request_id
        request = Request(scope)
        upload = (
            scope["method"] == "POST" and scope["path"].rstrip("/") == "/api/v1/documents/upload"
        )
        started = time.monotonic()
        if upload:
            logging.getLogger(__name__).info("Upload started: request=%s", request_id)

        async def send_response(message: Message) -> None:
            if message["type"] == "http.response.start":
                if upload:
                    logging.getLogger(__name__).info(
                        "Upload response: request=%s status=%s seconds=%.2f",
                        request_id,
                        message["status"],
                        time.monotonic() - started,
                    )
                headers = [
                    (k, v) for k, v in message.get("headers", []) if k.lower() != b"x-request-id"
                ]
                headers.append((b"x-request-id", request_id.encode()))
                if scope["path"].startswith("/api/v1/"):
                    headers = [(k, v) for k, v in headers if k.lower() != b"cache-control"]
                    headers.append((b"cache-control", b"no-store"))
                message["headers"] = headers
            await send(message)

        if scope["method"] == "POST" and scope["path"].rstrip("/") in {
            "/api/v1/auth/register",
            "/api/v1/auth/login",
        }:
            try:
                settings = request.app.state.settings
                await check_rate_limit(
                    request.app.state.session_factory,
                    "ip",
                    request.client.host if request.client else "unknown",
                    settings.auth_ip_limit,
                    settings.auth_rate_window_seconds,
                )
                body = bytearray()
                while True:
                    message = await receive()
                    if message["type"] == "http.disconnect":
                        return
                    chunk = message.get("body", b"")
                    if len(body) + len(chunk) > AUTH_BODY_LIMIT:
                        raise AppError(413, "payload_too_large", "Auth payload exceeds 16 KiB")
                    body.extend(chunk)
                    if not message.get("more_body", False):
                        break
            except AppError as error:
                await error_response(request, error)(scope, receive, send_response)
                return
            except (SQLAlchemyError, PostgresError, OSError, TimeoutError) as error:
                response = await database_error_handler(request, error)
                await response(scope, receive, send_response)
                return

            original_receive = receive
            consumed = False

            async def receive_body() -> Message:
                nonlocal consumed
                if not consumed:
                    consumed = True
                    return {"type": "http.request", "body": bytes(body), "more_body": False}
                return await original_receive()

            receive = receive_body
        await self.app(scope, receive, send_response)
