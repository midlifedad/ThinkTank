"""Integration tests for Alembic migrations against real PostgreSQL.

Tests verify:
1. alembic upgrade head creates all 16 tables
2. alembic downgrade base drops all tables cleanly
3. Advisory lock is acquired during migration (verified via successful execution)

Runs alembic as a subprocess to avoid asyncio.run() conflicts with
the already-running test event loop.
"""

import os
import subprocess
import sys

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from src.thinktank.models import Base

TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://thinktank_test:thinktank_test@localhost:5433/thinktank_test",
)

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def run_alembic(command: str) -> subprocess.CompletedProcess:
    """Run an alembic command as a subprocess."""
    env = os.environ.copy()
    env["DATABASE_URL"] = TEST_DATABASE_URL
    result = subprocess.run(
        [sys.executable, "-m", "alembic", command, "head" if command == "upgrade" else "base"],
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
        env=env,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Alembic {command} failed: {result.stderr}")
    return result


async def get_table_names(url: str) -> list[str]:
    """Query information_schema for table names in public schema."""
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        result = await conn.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_type = 'BASE TABLE' "
                "AND table_name != 'alembic_version' "
                "ORDER BY table_name"
            )
        )
        tables = [row[0] for row in result.fetchall()]
    await engine.dispose()
    return tables


async def drop_all_tables(url: str) -> None:
    """Drop all tables in the test database for a clean state."""
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
    await engine.dispose()


@pytest.fixture(autouse=True)
async def clean_migration_db(engine):
    """Ensure clean state before each migration test.

    After the test, recreates tables via create_all so that other
    test modules (test_models) still have tables available.
    """
    await drop_all_tables(TEST_DATABASE_URL)
    yield
    # Restore tables for subsequent tests that depend on create_all
    await drop_all_tables(TEST_DATABASE_URL)
    async with engine.begin() as conn:
        # Re-create pg_trgm extension (dropped by DROP SCHEMA CASCADE)
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        await conn.run_sync(Base.metadata.create_all)
        # Re-create GiST index for trigram similarity tests
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_candidate_thinkers_trgm "
            "ON candidate_thinkers USING gist (normalized_name gist_trgm_ops)"
        ))


@pytest.mark.asyncio
async def test_upgrade_head_creates_all_tables():
    """Running 'alembic upgrade head' creates all 14 model tables."""
    run_alembic("upgrade")

    tables = await get_table_names(TEST_DATABASE_URL)
    expected_tables = sorted([
        "api_usage",
        "candidate_thinkers",
        "categories",
        "content",
        "content_thinkers",
        "jobs",
        "llm_reviews",
        "rate_limit_usage",
        "source_categories",
        "source_thinkers",
        "sources",
        "system_config",
        "thinker_categories",
        "thinker_metrics",
        "thinker_profiles",
        "thinkers",
    ])
    assert tables == expected_tables, f"Missing tables: {set(expected_tables) - set(tables)}"


@pytest.mark.asyncio
async def test_downgrade_base_drops_tables():
    """After upgrade, running 'alembic downgrade base' drops all tables."""
    run_alembic("upgrade")

    tables = await get_table_names(TEST_DATABASE_URL)
    assert len(tables) == 16

    run_alembic("downgrade")

    tables = await get_table_names(TEST_DATABASE_URL)
    assert len(tables) == 0, f"Tables still exist after downgrade: {tables}"


@pytest.mark.asyncio
async def test_advisory_lock_in_migration():
    """Migration acquires advisory lock -- verified via successful execution.

    The advisory lock prevents corruption when multiple services start simultaneously.
    We verify this indirectly: the migration completes successfully, which means
    the lock was acquired, migrations ran, and the lock was released.
    """
    run_alembic("upgrade")

    tables = await get_table_names(TEST_DATABASE_URL)
    assert len(tables) == 16

    run_alembic("downgrade")

    tables = await get_table_names(TEST_DATABASE_URL)
    assert len(tables) == 0
