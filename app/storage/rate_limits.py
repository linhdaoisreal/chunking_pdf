import hashlib
import math
from datetime import timedelta

from sqlalchemy import case, delete, func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.errors import AppError
from app.storage.models import AuthRateLimit


async def check_rate_limit(
    factory: async_sessionmaker[AsyncSession],
    kind: str,
    value: str,
    limit: int,
    window_seconds: int,
) -> None:
    key_hash = hashlib.sha256(f"{kind}:{value}".encode()).hexdigest()
    window = timedelta(seconds=window_seconds)
    expired = AuthRateLimit.window_started_at <= func.now() - window
    async with factory.begin() as session:
        await session.execute(
            delete(AuthRateLimit).where(AuthRateLimit.window_started_at < func.now() - 2 * window)
        )
        row = (
            await session.execute(
                insert(AuthRateLimit)
                .values(key_hash=key_hash, window_started_at=func.now(), attempts=1)
                .on_conflict_do_update(
                    index_elements=[AuthRateLimit.key_hash],
                    set_={
                        "window_started_at": case(
                            (expired, func.now()), else_=AuthRateLimit.window_started_at
                        ),
                        "attempts": case((expired, 1), else_=AuthRateLimit.attempts + 1),
                    },
                )
                .returning(AuthRateLimit.attempts, AuthRateLimit.window_started_at, func.now())
            )
        ).one()
    # Commit the counter even when the request is denied.
    if row[0] > limit:
        retry_after = max(1, math.ceil((row[1] + window - row[2]).total_seconds()))
        raise AppError(429, "rate_limited", "Too many requests", {"Retry-After": str(retry_after)})
