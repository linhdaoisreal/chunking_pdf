from typing import Any
from uuid import UUID

from pydantic import BaseModel

from app.schemas.responses.common import ErrorDetail
from app.storage.models.documents import DocumentStatus


class UploadResponse(BaseModel):
    document_id: UUID
    status: DocumentStatus
    status_url: str
    chunks_url: str


class DocumentResponse(BaseModel):
    document_id: UUID
    status: DocumentStatus
    stage: str | None
    page_count: int | None
    chunk_count: int
    warnings: list[Any]
    quality_summary: dict[str, Any]
    processing_error: ErrorDetail | None
    retryable: bool
    source_available: bool


class ChunkResponse(BaseModel):
    chunk_id: UUID
    document_id: UUID
    chunk_index: int
    content: str
    token_count: int
    metadata: dict[str, Any]


class ChunkPageResponse(BaseModel):
    chunks: list[ChunkResponse]
    total: int
    limit: int
    offset: int
    next_offset: int | None
