"""FastAPI dependency injection for ThinkTank Admin."""

from collections.abc import AsyncGenerator
from pathlib import Path

from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from thinktank.database import async_session_factory
from thinktank.models.constants import (
    ERROR_CONTENT_STATUSES,
    HEALTHY_CONTENT_STATUSES,
    WARNING_CONTENT_STATUSES,
)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield an async database session, auto-closing after use."""
    async with async_session_factory() as session:
        yield session


def get_templates() -> Jinja2Templates:
    """Return Jinja2Templates configured for admin templates directory.

    Also installs the shared content-status tuples as Jinja globals so
    partials bucket statuses against the single source of truth in
    models/constants.py (see ADMIN-REVIEW CR-03/CR-04/HI-01). Templates
    and workers previously drifted (templates checked 'completed' /
    'transcribed' while workers wrote 'done' / 'cataloged') which caused
    healthy rows to render as neutral / unstyled.
    """
    templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
    templates.env.globals["HEALTHY_CONTENT_STATUSES"] = HEALTHY_CONTENT_STATUSES
    templates.env.globals["WARNING_CONTENT_STATUSES"] = WARNING_CONTENT_STATUSES
    templates.env.globals["ERROR_CONTENT_STATUSES"] = ERROR_CONTENT_STATUSES
    return templates
