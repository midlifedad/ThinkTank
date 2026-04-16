"""Shared test fixtures for ThinkTank test suite."""

import os
from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from src.thinktank.models import Base

TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://thinktank_test:thinktank_test@localhost:5433/thinktank_test",
)


@pytest.fixture(scope="session")
async def engine():
    """Session-scoped async engine for test database.

    Creates all tables at start, cleans up at end.
    """
    eng = create_async_engine(
        TEST_DATABASE_URL,
        echo=False,
        connect_args={"server_settings": {"timezone": "UTC"}},
    )

    # Create all tables from models (with pg_trgm extension for trigram similarity)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        await conn.run_sync(Base.metadata.create_all)
        # Create GiST index for trigram similarity on candidate_thinkers.normalized_name
        # (SQLAlchemy create_all does not run Alembic migrations, so we add it explicitly)
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_candidate_thinkers_trgm "
            "ON candidate_thinkers USING gist (normalized_name gist_trgm_ops)"
        ))

    yield eng

    # Cleanup and dispose
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await eng.dispose()


@pytest.fixture(scope="session")
def session_factory(engine):
    """Session factory bound to the test engine."""
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture
async def session(session_factory) -> AsyncGenerator[AsyncSession, None]:
    """Per-test async session.

    Each test gets its own session. Tables are truncated
    after each test for isolation.
    """
    async with session_factory() as session:
        yield session


@pytest.fixture
async def _cleanup_tables(engine):
    """Truncate all tables after each test for isolation.

    Uses IF EXISTS to handle cases where migration tests drop tables.
    Not autouse -- applied via integration/conftest.py to avoid
    requiring a database connection for pure unit tests.
    """
    yield
    try:
        async with engine.begin() as conn:
            # Use dynamic query to only truncate tables that exist
            result = await conn.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'public' "
                    "AND table_name != 'alembic_version'"
                )
            )
            existing_tables = [row[0] for row in result.fetchall()]
            if existing_tables:
                await conn.execute(
                    text(f"TRUNCATE {', '.join(existing_tables)} CASCADE")
                )
    except Exception:
        pass  # Tables may not exist (e.g., after migration downgrade tests)


@pytest.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    """HTTP client for integration tests against the FastAPI app."""
    # Override DATABASE_URL to use test database
    os.environ["DATABASE_URL"] = TEST_DATABASE_URL

    # Clear settings cache to pick up test URL
    from thinktank.config import get_settings

    get_settings.cache_clear()

    from thinktank.api.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    # Restore cache
    get_settings.cache_clear()


# Shared admin-auth test fixtures.
#
# ADMIN-REVIEW CR-01 gates every non-public admin route behind
# ``require_admin``, and ADMIN-REVIEW CR-02 follow-up gates the
# ``PUT /api/config/{key}`` write endpoint behind the same dependency.
# Tests that exercise those gated routes need a real admin token seeded
# in system_config and a convenience header dict to send. These live at
# the top level so both integration and contract suites can use them.
_ADMIN_TEST_TOKEN = "test-admin-token-abc123"


@pytest.fixture
async def seeded_admin_token(session) -> str:
    """Seed ``secret_admin_api_token`` into system_config and return the raw value.

    Tests that need to authenticate against real admin dependencies
    request this fixture plus (for API calls) ``authed_admin_headers``.
    """
    from tests.factories import create_system_config

    await create_system_config(
        session,
        key="secret_admin_api_token",
        value=_ADMIN_TEST_TOKEN,
        set_by="test",
    )
    await session.commit()
    return _ADMIN_TEST_TOKEN


@pytest.fixture
async def authed_admin_headers(seeded_admin_token) -> dict[str, str]:
    """Return an ``Authorization: Bearer <token>`` header for admin-gated routes."""
    return {"Authorization": f"Bearer {seeded_admin_token}"}
