"""Database engine and session factory for ThinkTank.

Uses settings from config.py for database URL and pool configuration.
Provides create_engine_from_url() for test overrides.
"""

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from thinktank.config import get_settings


def create_engine_from_url(url: str, **kwargs: object) -> AsyncEngine:
    """Create an async engine from a URL with optional overrides.

    Useful for tests that need a different database URL or pool settings.

    HANDLERS-REVIEW LO-06: forces every asyncpg connection to ``TIMEZONE=UTC``
    as a belt-and-suspenders complement to the ``NOW()`` queries in
    ``queue/reclaim.py`` and ``queue/rate_limiter.py``. ``NOW()`` already
    returns TIMESTAMPTZ (unambiguous), but several admin + rollup paths still
    use ``LOCALTIMESTAMP`` / ``NOW()::timestamp``; forcing the session tz
    keeps those correct too. Without it, a Postgres session default of the
    server's local tz (e.g. ``America/Los_Angeles`` on Railway-West) would
    make timezone-less timestamps diverge from the aware UTC datetimes
    Python writes via ``TIMESTAMPTZ`` columns.
    """
    settings = get_settings()
    defaults = {
        "pool_size": settings.db_pool_size,
        "max_overflow": settings.db_max_overflow,
        "pool_pre_ping": True,
        "connect_args": {"server_settings": {"timezone": "UTC"}},
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
