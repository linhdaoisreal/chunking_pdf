"""Source retention and orphan reconciliation; no queued/processing source is removed."""

import re
import time

from sqlalchemy import func, select
from starlette.concurrency import run_in_threadpool

from app.config import Settings
from app.services.files import remove_source, source_path
from app.storage.documents import source_guard
from app.storage.models import Document


async def cleanup_sources(factory, settings: Settings) -> int:
    removed = 0
    async with factory.begin() as session:
        expired = (
            await session.scalars(
                select(Document)
                .where(
                    Document.status.in_(["completed", "failed"]),
                    Document.source_key.is_not(None),
                    Document.source_expires_at <= func.clock_timestamp(),
                )
                .order_by(Document.source_expires_at)
                .limit(100)
                .with_for_update(skip_locked=True)
            )
        ).all()
        for document in expired:
            await run_in_threadpool(remove_source, settings.pdf_storage_dir, document.source_key)
            document.source_key = None
            removed += 1
    async with source_guard(factory, exclusive=True) as session:
        keys = set(
            (
                await session.scalars(
                    select(Document.source_key).where(Document.source_key.is_not(None))
                )
            ).all()
        )
        candidates = await run_in_threadpool(lambda: list(settings.pdf_storage_dir.glob("*.pdf")))
        for path in candidates:
            if (
                path.name in keys
                or path.is_symlink()
                or re.fullmatch(r"[0-9a-f]{32}\.pdf", path.name) is None
            ):
                continue
            safe = source_path(settings.pdf_storage_dir, path.name)
            if time.time() - safe.stat().st_mtime < settings.orphan_grace_seconds:
                continue
            await run_in_threadpool(remove_source, settings.pdf_storage_dir, path.name)
            removed += 1
    return removed
