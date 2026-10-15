"""Durable PostgreSQL worker with isolated PDF processing."""

import argparse
import asyncio
import logging
import multiprocessing
import time
from contextlib import suppress

import psutil
import tiktoken
from asyncpg import PostgresError
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from starlette.concurrency import run_in_threadpool

from app.config import Settings
from app.logging_config import configure_logging
from app.pipelines.ingestion import ingest
from app.pipelines.state import ProcessingError, State
from app.services.files import source_path
from app.services.pdf import check_ocr
from app.storage import jobs
from app.storage.maintenance import cleanup_sources
from app.storage.session import check_connection, create_engine, create_session_factory

logger = logging.getLogger("app.worker")


def process_document(connection, claim: jobs.Claim, settings: Settings) -> None:
    configure_logging()
    state = State(document_id=claim.document_id)
    try:
        values = settings.model_dump()
        # Resolve null engine/tokenizer in uploads created before local OCR was selected.
        values.update(
            {
                key: value
                for key, value in claim.processing_config.items()
                if key in Settings.model_fields and value is not None
            }
        )
        settings = Settings(_env_file=None, **values)
        state.processing_config = settings.processing_snapshot()
        if not claim.source_key:
            raise ProcessingError("source_missing", "Source PDF is unavailable")
        path = source_path(settings.pdf_storage_dir, claim.source_key)
        if not path.is_file():
            raise ProcessingError("source_missing", "Source PDF is unavailable")
        state = ingest(state, path, settings, lambda stage: connection.send(("stage", stage)))
        connection.send(("result", state))
    except (BrokenPipeError, EOFError):
        return
    except ProcessingError as error:
        connection.send(("error", (error.code, error.message, error.retryable, state)))
    except Exception:
        connection.send(("error", ("processing_failed", "PDF processing failed", False, state)))
    finally:
        connection.close()


def kill_tree(process) -> None:
    if process.pid is None:
        return
    if not process.is_alive():
        process.join(timeout=3)
        return
    with suppress(psutil.NoSuchProcess):
        parent = psutil.Process(process.pid)
        children = parent.children(recursive=True)
        for child in reversed(children):
            with suppress(psutil.NoSuchProcess):
                child.kill()
        with suppress(psutil.NoSuchProcess):
            parent.kill()
        psutil.wait_procs(children, timeout=3)
    process.join(timeout=3)


async def run_claim(factory, claim: jobs.Claim, settings: Settings) -> None:
    logger.info(
        "Document claimed: document=%s job=%s attempt=%s",
        claim.document_id,
        claim.job_id,
        claim.attempt,
    )
    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(target=process_document, args=(sender, claim, settings))
    process.start()
    sender.close()
    started = time.monotonic()
    next_heartbeat = started
    stage = "parsing"
    try:
        while True:
            now = time.monotonic()
            if now - started > settings.worker_deadline_seconds:
                raise ProcessingError("processing_timeout", "Processing deadline exceeded", True)
            if now >= next_heartbeat:
                await jobs.heartbeat(factory, claim, settings, stage)
                logger.info(
                    "Worker active: document=%s job=%s stage=%s seconds=%.2f",
                    claim.document_id,
                    claim.job_id,
                    stage,
                    now - started,
                )
                next_heartbeat = now + settings.worker_heartbeat_seconds
            if receiver.poll():
                try:
                    kind, payload = await run_in_threadpool(receiver.recv)
                except EOFError:
                    raise ProcessingError(
                        "worker_crashed", "Processing subprocess exited", True
                    ) from None
                if kind == "stage":
                    stage = payload
                    logger.info(
                        "Worker stage: document=%s job=%s stage=%s",
                        claim.document_id,
                        claim.job_id,
                        stage,
                    )
                    await jobs.heartbeat(factory, claim, settings, stage)
                elif kind == "result":
                    stage = "saving"
                    logger.info(
                        "Saving chunks: document=%s job=%s chunks=%s",
                        claim.document_id,
                        claim.job_id,
                        len(payload.chunks),
                    )
                    await jobs.heartbeat(factory, claim, settings, "saving")
                    await jobs.complete(factory, claim, settings, payload)
                    logger.info(
                        "Document completed: document=%s job=%s attempt=%s "
                        "pages=%s chunks=%s seconds=%.2f",
                        claim.document_id,
                        claim.job_id,
                        claim.attempt,
                        payload.page_count,
                        len(payload.chunks),
                        time.monotonic() - started,
                    )
                    return
                elif kind == "error":
                    code, message, retryable, state = payload
                    await jobs.fail(
                        factory, claim, settings, ProcessingError(code, message, retryable), state
                    )
                    logger.warning(
                        "Document failed: document=%s job=%s stage=%s code=%s",
                        claim.document_id,
                        claim.job_id,
                        stage,
                        code,
                    )
                    return
            if not process.is_alive() and not receiver.poll():
                raise ProcessingError("worker_crashed", "Processing subprocess exited", True)
            with suppress(psutil.NoSuchProcess):
                parent = psutil.Process(process.pid)
                usage = parent.memory_info().rss
                for child in parent.children(recursive=True):
                    with suppress(psutil.NoSuchProcess):
                        usage += child.memory_info().rss
                if usage > settings.worker_max_memory_mb * 1024 * 1024:
                    raise ProcessingError("memory_limit", "Processing exceeded memory limit")
            await asyncio.sleep(0.1)
    except (BrokenPipeError, EOFError):
        with suppress(jobs.LeaseLost):
            await jobs.fail(
                factory,
                claim,
                settings,
                ProcessingError("worker_crashed", "Processing subprocess exited", True),
            )
    except ProcessingError as error:
        logger.warning(
            "Worker failed: document=%s job=%s stage=%s code=%s",
            claim.document_id,
            claim.job_id,
            stage,
            error.code,
        )
        await run_in_threadpool(kill_tree, process)
        with suppress(jobs.LeaseLost):
            await jobs.fail(factory, claim, settings, error)
    except jobs.LeaseLost:
        logger.warning("Worker lost lease: job=%s", claim.job_id)
    finally:
        await run_in_threadpool(kill_tree, process)
        receiver.close()
        process.close()


async def preflight(settings: Settings) -> None:
    engine = create_engine(settings)
    try:
        await check_connection(engine)
        await run_in_threadpool(check_ocr, settings)
        await run_in_threadpool(tiktoken.get_encoding, settings.tokenizer_name)
    finally:
        await engine.dispose()


async def run_worker(settings: Settings, once: bool = False) -> None:
    logger.info("Worker starting: checking database, OCR and tokenizer")
    await preflight(settings)
    logger.info("Worker ready: slots=%s; waiting for queued documents", settings.worker_concurrency)
    engine = create_engine(settings)
    factory = create_session_factory(engine)

    async def slot() -> None:
        while True:
            try:
                claim = await jobs.claim_next(factory, settings)
                if claim:
                    await run_claim(factory, claim, settings)
                if once:
                    return
                if claim is None:
                    await asyncio.sleep(settings.worker_poll_seconds)
            except (SQLAlchemyError, PostgresError, OSError):
                logger.warning("Worker dependency unavailable")
                if once:
                    raise
                await asyncio.sleep(settings.worker_poll_seconds)

    async def maintenance() -> None:
        while True:
            try:
                await cleanup_sources(factory, settings)
            except (SQLAlchemyError, PostgresError, OSError):
                logger.warning("Source cleanup deferred")
            await asyncio.sleep(60)

    try:
        async with asyncio.TaskGroup() as group:
            if not once:
                group.create_task(maintenance())
            for _ in range(settings.worker_concurrency):
                group.create_task(slot())
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Check DB, OCR and tokenizer")
    parser.add_argument("--once", action="store_true", help="Process at most one job per slot")
    args = parser.parse_args()
    configure_logging()
    try:
        settings = Settings()
        asyncio.run(preflight(settings) if args.check else run_worker(settings, args.once))
    except (ValidationError, ProcessingError):
        parser.exit(1, "Invalid processing configuration; check OCR, tokenizer and ENV.\n")
    except (SQLAlchemyError, PostgresError, OSError, TimeoutError, ExceptionGroup):
        parser.exit(1, "Worker dependency failed; check PostgreSQL and local processing setup.\n")
    except KeyboardInterrupt:
        return
    if args.check:
        print("Worker database, OCR and tokenizer checks passed.")


if __name__ == "__main__":
    main()
