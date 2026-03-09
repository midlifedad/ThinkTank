"""Shared test fixtures for ThinkTank test suite."""

import os
from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://thinktank_test:thinktank_test@localhost:5433/thinktank_test",
)


@pytest.fixture(scope="session")
async def engine():
    """Session-scoped async engine for test database."""
    eng = create_async_engine(TEST_DATABASE_URL, echo=False)
    # Verify connectivity
    async with eng.begin() as conn:
        await conn.execute(text("SELECT 1"))
    yield eng
    await eng.dispose()


@pytest.fixture
async def session(engine) -> AsyncGenerator[AsyncSession, None]:
    """Per-test session with transaction rollback for isolation."""
    async with engine.connect() as conn:
        trans = await conn.begin()
        session = AsyncSession(bind=conn, expire_on_commit=False)
        yield session
        await session.close()
        await trans.rollback()


@pytest.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    """HTTP client for integration tests against the FastAPI app."""
    # Override DATABASE_URL to use test database
    os.environ["DATABASE_URL"] = TEST_DATABASE_URL

    from thinktank.api.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
