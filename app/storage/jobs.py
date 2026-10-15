"""Short transactions with lease fencing for every worker write."""

from dataclasses import asdict, dataclass
from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.pipelines.state import ProcessingError, State
from app.storage.models import Document, DocumentChunk, ProcessingJob


class LeaseLost(Exception):
    pass


@dataclass(frozen=True)
class Claim:
    job_id: UUID
    document_id: UUID
    lease_token: UUID
    source_key: str | None
    processing_config: dict
    attempt: int


async def locked_job(session: AsyncSession, claim: Claim) -> ProcessingJob:
    job = await session.scalar(
        select(ProcessingJob)
        .where(
            ProcessingJob.id == claim.job_id,
            ProcessingJob.status == "processing",
            ProcessingJob.lease_token == claim.lease_token,
            ProcessingJob.lease_expires_at > func.clock_timestamp(),
        )
        .with_for_update()
    )
    if job is None:
        raise LeaseLost()
    return job


def finish_failure(
    job: ProcessingJob, document: Document, settings: Settings, error: ProcessingError, now
) -> None:
    job.error_code, job.error_message, job.retryable = error.code, error.message, error.retryable
    job.lease_token = job.lease_expires_at = None
    if error.retryable and job.attempt_count < settings.worker_max_attempts:
        document.status = job.status = "queued"
        job.stage = None
        delay = settings.worker_retry_backoff_seconds * 2 ** min(job.attempt_count - 1, 10)
        job.next_attempt_at = now + timedelta(seconds=delay)
    else:
        document.status = job.status = "failed"
        document.source_expires_at = now + timedelta(
            seconds=settings.source_failed_retention_seconds
        )


async def claim_next(factory: async_sessionmaker[AsyncSession], settings: Settings) -> Claim | None:
    async with factory.begin() as session:
        now = await session.scalar(select(func.clock_timestamp()))
        expired = (
            await session.scalars(
                select(ProcessingJob)
                .where(
                    ProcessingJob.status == "processing",
                    ProcessingJob.lease_expires_at <= now,
                )
                .order_by(ProcessingJob.lease_expires_at)
                .limit(100)
                .with_for_update(skip_locked=True)
            )
        ).all()
        for job in expired:
            document = await session.get(Document, job.document_id, with_for_update=True)
            finish_failure(
                job,
                document,
                settings,
                ProcessingError("lease_expired", "Worker lease expired", True),
                now,
            )
        await session.flush()
        job = await session.scalar(
            select(ProcessingJob)
            .where(
                ProcessingJob.status == "queued",
                ProcessingJob.next_attempt_at <= now,
            )
            .order_by(ProcessingJob.next_attempt_at, ProcessingJob.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if job is None:
            return None
        document = await session.get(Document, job.document_id, with_for_update=True)
        if document.status != "queued":
            raise RuntimeError("Document/job status mismatch")
        document.status = job.status = "processing"
        document.source_expires_at = None
        job.stage = "parsing"
        job.attempt_count += 1
        job.lease_token = uuid4()
        job.lease_expires_at = now + timedelta(seconds=settings.worker_lease_seconds)
        job.error_code = job.error_message = None
        return Claim(
            job.id,
            document.id,
            job.lease_token,
            document.source_key,
            document.processing_config,
            job.attempt_count,
        )


async def heartbeat(
    factory: async_sessionmaker[AsyncSession], claim: Claim, settings: Settings, stage: str
) -> None:
    async with factory.begin() as session:
        job = await locked_job(session, claim)
        now = await session.scalar(select(func.clock_timestamp()))
        job.lease_expires_at = now + timedelta(seconds=settings.worker_lease_seconds)
        job.stage = stage


async def complete(
    factory: async_sessionmaker[AsyncSession], claim: Claim, settings: Settings, state: State
) -> None:
    if state.document_id != claim.document_id or not state.chunks:
        raise ProcessingError("invalid_result", "Ingestion produced no valid result")
    async with factory.begin() as session:
        job = await locked_job(session, claim)
        document = await session.get(Document, claim.document_id, with_for_update=True)
        await session.execute(delete(DocumentChunk).where(DocumentChunk.document_id == document.id))
        for index, chunk in enumerate(state.chunks):
            metadata = asdict(chunk)
            metadata.pop("content")
            metadata.pop("token_count")
            session.add(
                DocumentChunk(
                    document_id=document.id,
                    chunk_index=index,
                    content=chunk.content,
                    token_count=chunk.token_count,
                    metadata_=metadata,
                )
            )
        await session.flush()
        # Saving may outlast the lease; never publish results under an expired token.
        now = await session.scalar(select(func.clock_timestamp()))
        if job.lease_expires_at <= now:
            raise LeaseLost()
        document.status = "completed"
        document.page_count = state.page_count
        document.chunk_count = len(state.chunks)
        document.warnings = list(dict.fromkeys(state.warnings))
        document.quality_summary = state.quality_summary
        document.processing_config = state.processing_config
        document.metadata_ = {
            "blocks": [
                {
                    "block_id": block.block_id,
                    "page_number": block.page_number,
                    "length": len(block.text),
                    "source_method": block.source_method,
                }
                for block in state.blocks
            ]
        }
        document.source_expires_at = now + timedelta(
            seconds=settings.source_completed_retention_seconds
        )
        job.status, job.stage, job.retryable = "succeeded", "completed", False
        job.lease_token = job.lease_expires_at = None
        job.error_code = job.error_message = None


async def fail(
    factory: async_sessionmaker[AsyncSession],
    claim: Claim,
    settings: Settings,
    error: ProcessingError,
    state: State | None = None,
) -> None:
    async with factory.begin() as session:
        job = await locked_job(session, claim)
        document = await session.get(Document, claim.document_id, with_for_update=True)
        now = await session.scalar(select(func.clock_timestamp()))
        finish_failure(job, document, settings, error, now)
        if state:
            document.page_count = state.page_count or None
            document.warnings = list(dict.fromkeys(state.warnings))
