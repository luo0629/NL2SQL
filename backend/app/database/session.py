from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.engine import async_session_factory


async def get_async_db() -> AsyncIterator[AsyncSession]:
    async with async_session_factory() as session:
        yield session
