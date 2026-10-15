import asyncio
import logging
import time
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Query, Request, Response
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException
from starlette.formparsers import MultiPartException

from app.api.dependencies import CurrentUser
from app.errors import AppError
from app.schemas.responses.common import ApiResponse, ErrorDetail
from app.schemas.responses.documents import (
    ChunkPageResponse,
    ChunkResponse,
    DocumentResponse,
    UploadResponse,
)
from app.services.files import remove_source, save_pdf, source_available
from app.storage import documents
from app.storage.rate_limits import check_rate_limit

router = APIRouter(prefix="/api/v1/documents", tags=["documents"])
logger = logging.getLogger(__name__)


@router.get("/{document_id}/chunks", response_model=ApiResponse[ChunkPageResponse])
async def chunks(
    document_id: UUID,
    request: Request,
    user: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ApiResponse[ChunkPageResponse]:
    rows, total = await documents.get_chunks(
        request.app.state.session_factory, user.id, document_id, limit, offset
    )
    return ApiResponse(
        statusCode=200,
        data=ChunkPageResponse(
            chunks=[
                ChunkResponse(
                    chunk_id=row.id,
                    document_id=row.document_id,
                    chunk_index=row.chunk_index,
                    content=row.content,
                    token_count=row.token_count,
                    metadata=row.metadata_,
                )
                for row in rows
            ],
            total=total,
            limit=limit,
            offset=offset,
            next_offset=offset + len(rows) if offset + len(rows) < total else None,
        ),
    )


@router.post("/{document_id}/retry", status_code=202, response_model=ApiResponse[UploadResponse])
async def retry(
    document_id: UUID, request: Request, response: Response, user: CurrentUser
) -> ApiResponse[UploadResponse]:
    factory = request.app.state.session_factory
    settings = request.app.state.settings
    await documents.get_status(factory, user.id, document_id)
    await check_rate_limit(
        factory,
        "document_retry",
        str(user.id),
        settings.retry_owner_limit,
        settings.auth_rate_window_seconds,
    )
    document = await documents.retry(factory, user.id, document_id, settings)
    url = f"/api/v1/documents/{document.id}"
    response.headers["Location"] = url
    return ApiResponse(
        statusCode=202,
        data=UploadResponse(
            document_id=document.id,
            status=document.status,
            status_url=url,
            chunks_url=f"{url}/chunks",
        ),
    )


@router.post(
    "/upload",
    status_code=202,
    response_model=ApiResponse[UploadResponse],
    responses={200: {"model": ApiResponse[UploadResponse]}},
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "required": ["file"],
                        "properties": {"file": {"type": "string", "format": "binary"}},
                    }
                }
            },
        }
    },
)
async def upload(
    request: Request,
    response: Response,
    user: CurrentUser,
    idempotency_key: Annotated[
        str, Header(min_length=1, max_length=200, pattern=r"^[\x21-\x7e]+$")
    ],
) -> ApiResponse[UploadResponse]:
    settings = request.app.state.settings
    if request.headers.get("content-type", "").split(";")[0].lower() != "multipart/form-data":
        raise AppError(415, "invalid_media_type", "Expected multipart/form-data")
    request_id = request.state.request_id
    logger.info("Upload receiving: request=%s", request_id)
    received = 0
    next_log = time.monotonic() + 5
    failure: AppError | None = None
    deadline = asyncio.get_running_loop().time() + settings.upload_timeout_seconds

    async def limited_receive():
        nonlocal received, failure, next_log
        try:
            remaining = max(0, deadline - asyncio.get_running_loop().time())
            message = await asyncio.wait_for(request.receive(), remaining)
        except TimeoutError:
            failure = AppError(408, "upload_timeout", "Upload deadline exceeded")
            raise MultiPartException("Upload deadline exceeded") from None
        if message["type"] == "http.disconnect":
            failure = AppError(400, "upload_interrupted", "Upload interrupted")
            raise MultiPartException("Upload interrupted")
        received += len(message.get("body", b""))
        if received > settings.upload_max_bytes + 64 * 1024:
            failure = AppError(413, "payload_too_large", "Multipart exceeds upload limit")
            raise MultiPartException("Multipart exceeds upload limit")
        if time.monotonic() >= next_log:
            logger.info("Upload receiving: request=%s bytes=%s", request_id, received)
            next_log = time.monotonic() + 5
        return message

    bounded = Request(request.scope, receive=limited_receive)
    try:
        async with bounded.form(max_files=1, max_fields=0) as form:
            logger.info("Upload multipart parsed: request=%s bytes=%s", request_id, received)
            file = form.get("file")
            if not isinstance(file, UploadFile) or len(form) != 1:
                raise AppError(422, "invalid_upload", "Exactly one file field is required")
            if file.content_type != "application/pdf":
                raise AppError(415, "invalid_pdf", "File MIME must be application/pdf")
            logger.info("Upload waiting for storage: request=%s", request_id)
            async with documents.source_guard(request.app.state.session_factory) as upload_session:
                logger.info("Upload saving file: request=%s", request_id)
                saved = await run_in_threadpool(
                    save_pdf,
                    file.file,
                    settings.pdf_storage_dir,
                    settings.upload_max_bytes,
                )
                logger.info("Upload file saved; enqueueing: request=%s", request_id)
                try:
                    document, created = await documents.enqueue(
                        request.app.state.session_factory,
                        owner_id=user.id,
                        filename=(file.filename or "document.pdf")[:255],
                        content_hash=saved.content_hash,
                        idempotency_key=idempotency_key,
                        source_key=saved.key,
                        processing_config=settings.processing_snapshot(),
                        session=upload_session,
                    )
                except BaseException:
                    # A lost commit acknowledgement may still mean the source is referenced.
                    try:
                        referenced = await documents.source_is_referenced(
                            request.app.state.session_factory,
                            saved.key,
                        )
                        if not referenced:
                            await run_in_threadpool(
                                remove_source, settings.pdf_storage_dir, saved.key
                            )
                    except Exception:
                        # Retain on DB outage; maintenance reconciles unreferenced sources.
                        logger.warning("Source cleanup deferred", extra={"source_key": saved.key})
                    raise
                if not created:
                    await run_in_threadpool(remove_source, settings.pdf_storage_dir, saved.key)
    except HTTPException as error:
        reasons = {
            "Missing boundary in multipart.": "missing_boundary",
            "Too many files. Maximum number of files is 1.": "too_many_files",
            "Too many fields. Maximum number of fields is 0.": "unexpected_text_field",
            'The Content-Disposition header field "name" must be provided.': "missing_field_name",
            "Invalid multipart data.": "invalid_multipart",
        }
        reason = reasons.get(str(error.detail), "invalid_multipart")
        logger.warning(
            "Upload multipart rejected: request=%s bytes=%s reason=%s",
            request_id,
            received,
            failure.code if failure else reason,
        )
        if failure is not None:
            raise failure from None
        raise
    logger.info(
        "Upload accepted: request=%s document=%s created=%s status=%s bytes=%s",
        request_id,
        document.id,
        created,
        document.status,
        received,
    )
    status_code = 202 if document.status in ("queued", "processing") else 200
    status_url = f"/api/v1/documents/{document.id}"
    response.status_code = status_code
    response.headers["Location"] = status_url
    return ApiResponse(
        statusCode=status_code,
        data=UploadResponse(
            document_id=document.id,
            status=document.status,
            status_url=status_url,
            chunks_url=f"{status_url}/chunks",
        ),
    )


@router.get("/{document_id}", response_model=ApiResponse[DocumentResponse])
async def status(
    document_id: UUID,
    request: Request,
    user: CurrentUser,
) -> ApiResponse[DocumentResponse]:
    document, job = await documents.get_status(
        request.app.state.session_factory,
        user.id,
        document_id,
    )
    available = await run_in_threadpool(
        source_available,
        request.app.state.settings.pdf_storage_dir,
        document.source_key,
        document.source_expires_at,
    )
    return ApiResponse(
        statusCode=200,
        data=DocumentResponse(
            document_id=document.id,
            status=document.status,
            stage=job.stage,
            page_count=document.page_count,
            chunk_count=document.chunk_count,
            warnings=document.warnings,
            quality_summary=document.quality_summary,
            processing_error=ErrorDetail(code=job.error_code, message=job.error_message or "")
            if job.error_code
            else None,
            retryable=job.retryable,
            source_available=available,
        ),
    )
