from contextlib import asynccontextmanager
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.concurrency import run_in_threadpool

from app.config import Settings
from app.errors import AppError
from app.services.files import source_available
from app.storage.models import Document, DocumentChunk, ProcessingJob


@asynccontextmanager
async def source_guard(factory: async_sessionmaker[AsyncSession], exclusive: bool = False):
    # Serialize orphan reconciliation against save+enqueue, across every API process.
    async with factory.begin() as session:
        lock = func.pg_advisory_xact_lock if exclusive else func.pg_advisory_xact_lock_shared
        await session.execute(select(lock(714032)))
        yield session


async def get_chunks(
    factory: async_sessionmaker[AsyncSession],
    owner_id: UUID,
    document_id: UUID,
    limit: int,
    offset: int,
) -> tuple[list[DocumentChunk], int]:
    async with factory() as session:
        document = await session.scalar(
            select(Document).where(Document.id == document_id, Document.owner_id == owner_id)
        )
        if document is None:
            raise AppError(404, "document_not_found", "Document not found")
        if document.status != "completed":
            raise AppError(409, "document_not_completed", "Chunks are not available yet")
        chunks = (
            await session.scalars(
                select(DocumentChunk)
                .where(DocumentChunk.document_id == document_id)
                .order_by(DocumentChunk.chunk_index)
                .limit(limit)
                .offset(offset)
            )
        ).all()
        return list(chunks), document.chunk_count


async def retry(
    factory: async_sessionmaker[AsyncSession], owner_id: UUID, document_id: UUID, settings: Settings
) -> Document:
    async with factory.begin() as session:
        job = await session.scalar(
            select(ProcessingJob)
            .join(Document, Document.id == ProcessingJob.document_id)
            .where(Document.id == document_id, Document.owner_id == owner_id)
            .with_for_update(of=ProcessingJob)
        )
        if job is None:
            raise AppError(404, "document_not_found", "Document not found")
        document = await session.get(Document, document_id, with_for_update=True)
        if document.status in ("queued", "processing"):
            return document
        if document.status != "failed" or not job.retryable:
            raise AppError(409, "retry_unavailable", "Document cannot be retried")
        if not await run_in_threadpool(
            source_available,
            settings.pdf_storage_dir,
            document.source_key,
            document.source_expires_at,
        ):
            raise AppError(410, "source_expired", "Source unavailable; upload again with a new key")
        document.status = job.status = "queued"
        document.source_expires_at = None
        job.stage = job.error_code = job.error_message = None
        job.lease_token = job.lease_expires_at = None
        job.attempt_count = 0
        job.next_attempt_at = func.now()
        job.retryable = False
        return document


async def source_is_referenced(factory: async_sessionmaker[AsyncSession], key: str) -> bool:
    async with factory() as session:
        return (
            await session.scalar(select(Document.id).where(Document.source_key == key)) is not None
        )


async def enqueue(
    factory: async_sessionmaker[AsyncSession],
    *,
    owner_id: UUID,
    filename: str,
    content_hash: str,
    idempotency_key: str,
    source_key: str,
    processing_config: dict,
    session: AsyncSession | None = None,
) -> tuple[Document, bool]:
    if session is None:
        async with factory.begin() as session:
            return await enqueue(
                factory,
                owner_id=owner_id,
                filename=filename,
                content_hash=content_hash,
                idempotency_key=idempotency_key,
                source_key=source_key,
                processing_config=processing_config,
                session=session,
            )
    document = await session.scalar(
        insert(Document)
        .values(
            owner_id=owner_id,
            filename=filename,
            content_hash=content_hash,
            idempotency_key=idempotency_key,
            source_key=source_key,
            processing_config=processing_config,
            metadata_={},
        )
        .on_conflict_do_nothing(index_elements=[Document.owner_id, Document.idempotency_key])
        .returning(Document)
    )
    if document is not None:
        session.add(ProcessingJob(document_id=document.id))
        return document, True
    document = await session.scalar(
        select(Document).where(
            Document.owner_id == owner_id,
            Document.idempotency_key == idempotency_key,
        )
    )
    if document.content_hash != content_hash:
        raise AppError(409, "idempotency_conflict", "Key already used for a different PDF")
    return document, False


async def get_status(
    factory: async_sessionmaker[AsyncSession],
    owner_id: UUID,
    document_id: UUID,
) -> tuple[Document, ProcessingJob]:
    async with factory() as session:
        row = (
            await session.execute(
                select(Document, ProcessingJob)
                .join(ProcessingJob, ProcessingJob.document_id == Document.id)
                .where(Document.id == document_id, Document.owner_id == owner_id)
            )
        ).one_or_none()
        if row is None:
            raise AppError(404, "document_not_found", "Document not found")
        return row[0], row[1]
