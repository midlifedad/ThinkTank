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
    eng = create_async_engine(TEST_DATABASE_URL, echo=False)

    # Create all tables from models
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

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


@pytest.fixture(autouse=True)
async def _cleanup_tables(engine):
    """Truncate all tables after each test for isolation.

    Uses IF EXISTS to handle cases where migration tests drop tables.
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
