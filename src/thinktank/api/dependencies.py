"""FastAPI dependency injection for ThinkTank."""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession

from thinktank.database import async_session_factory


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield an async database session, auto-closing after use."""
    async with async_session_factory() as session:
        yield session
