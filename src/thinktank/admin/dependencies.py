"""FastAPI dependency injection for ThinkTank Admin."""

from collections.abc import AsyncGenerator
from pathlib import Path

from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from thinktank.database import async_session_factory


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield an async database session, auto-closing after use."""
    async with async_session_factory() as session:
        yield session


def get_templates() -> Jinja2Templates:
    """Return Jinja2Templates configured for admin templates directory."""
    return Jinja2Templates(directory=Path(__file__).parent / "templates")
