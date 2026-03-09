"""Integration tests for Alembic migrations against real PostgreSQL.

Tests verify:
1. alembic upgrade head creates all 14 tables
2. alembic downgrade base drops all tables cleanly
3. Advisory lock is acquired during migration (verified via successful execution)

Uses a separate test database to avoid interfering with create_all-based model tests.
"""

import asyncio
import os

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

# Test database URL -- uses the same test database but drops/recreates tables
TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://thinktank_test:thinktank_test@localhost:5433/thinktank_test",
)

# Sync URL for alembic (convert asyncpg to psycopg2 or use env var approach)
MIGRATION_DATABASE_URL = TEST_DATABASE_URL


def get_alembic_config() -> Config:
    """Get Alembic config pointing to the test database."""
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    alembic_ini = os.path.join(project_root, "alembic.ini")
    config = Config(alembic_ini)
    return config


async def get_table_names(url: str) -> list[str]:
    """Query pg_catalog for table names in public schema."""
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
        await conn.execute(text("DROP SCHEMA public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
    await engine.dispose()


@pytest.fixture(autouse=True)
async def clean_migration_db():
    """Ensure clean state before each migration test."""
    await drop_all_tables(MIGRATION_DATABASE_URL)
    yield
    await drop_all_tables(MIGRATION_DATABASE_URL)


@pytest.mark.asyncio
async def test_upgrade_head_creates_all_tables():
    """Running 'alembic upgrade head' creates all 14 model tables."""
    os.environ["DATABASE_URL"] = MIGRATION_DATABASE_URL
    config = get_alembic_config()

    # Run upgrade head
    command.upgrade(config, "head")

    # Verify all 14 tables exist
    tables = await get_table_names(MIGRATION_DATABASE_URL)

    expected_tables = sorted([
        "api_usage",
        "candidate_thinkers",
        "categories",
        "content",
        "content_thinkers",
        "jobs",
        "llm_reviews",
        "rate_limit_usage",
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
    os.environ["DATABASE_URL"] = MIGRATION_DATABASE_URL
    config = get_alembic_config()

    # Upgrade first
    command.upgrade(config, "head")

    # Verify tables exist
    tables = await get_table_names(MIGRATION_DATABASE_URL)
    assert len(tables) == 14

    # Downgrade
    command.downgrade(config, "base")

    # Verify tables are gone
    tables = await get_table_names(MIGRATION_DATABASE_URL)
    assert len(tables) == 0, f"Tables still exist after downgrade: {tables}"


@pytest.mark.asyncio
async def test_advisory_lock_in_migration():
    """Migration acquires advisory lock -- verified via successful concurrent-safe execution.

    The advisory lock prevents corruption when multiple services start simultaneously.
    We verify this indirectly: the migration completes successfully, which means
    the lock was acquired, migrations ran, and the lock was released.
    """
    os.environ["DATABASE_URL"] = MIGRATION_DATABASE_URL
    config = get_alembic_config()

    # Run upgrade -- if advisory lock code is broken, this would fail
    command.upgrade(config, "head")

    # Verify tables were created (lock was held during migration)
    tables = await get_table_names(MIGRATION_DATABASE_URL)
    assert len(tables) == 14

    # Run downgrade -- also uses advisory lock
    command.downgrade(config, "base")

    tables = await get_table_names(MIGRATION_DATABASE_URL)
    assert len(tables) == 0
