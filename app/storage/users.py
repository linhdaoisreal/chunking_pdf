from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.errors import AppError
from app.storage.models import User


async def get_by_email(factory: async_sessionmaker[AsyncSession], email: str) -> User | None:
    async with factory() as session:
        return await session.scalar(select(User).where(User.email == email))


async def get_by_id(factory: async_sessionmaker[AsyncSession], user_id: UUID) -> User | None:
    async with factory() as session:
        return await session.get(User, user_id)


async def create_user(
    factory: async_sessionmaker[AsyncSession],
    email: str,
    password_hash: str,
    display_name: str | None,
) -> User:
    async with factory.begin() as session:
        user = await session.scalar(
            insert(User)
            .values(email=email, password_hash=password_hash, display_name=display_name)
            .on_conflict_do_nothing(index_elements=[User.email])
            .returning(User)
        )
        if user is None:
            raise AppError(409, "email_exists", "Email is already registered")
        return user
