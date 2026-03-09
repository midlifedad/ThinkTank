"""Database engine and session factory for ThinkTank.

Uses settings from config.py for database URL and pool configuration.
Provides create_engine_from_url() for test overrides.
"""

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from thinktank.config import get_settings


def create_engine_from_url(url: str, **kwargs: object) -> AsyncEngine:
    """Create an async engine from a URL with optional overrides.

    Useful for tests that need a different database URL or pool settings.
    """
    settings = get_settings()
    defaults = {
        "pool_size": settings.db_pool_size,
        "max_overflow": settings.db_max_overflow,
        "pool_pre_ping": True,
    }
    defaults.update(kwargs)
    return create_async_engine(url, **defaults)


settings = get_settings()
engine = create_engine_from_url(settings.database_url)

async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)
