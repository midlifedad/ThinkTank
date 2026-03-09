"""Alembic async migration environment with advisory lock.

This module configures Alembic to:
1. Run migrations asynchronously using asyncpg
2. Acquire a PostgreSQL advisory lock before running migrations
   to prevent concurrent migration corruption
3. Read DATABASE_URL from environment variable
4. Import all models so autogenerate discovers all tables
"""

import asyncio
import os

from alembic import context
from sqlalchemy import pool, text
from sqlalchemy.ext.asyncio import create_async_engine

# Import all models so autogenerate can discover them via Base.metadata
from src.thinktank.models import Base  # noqa: F401

# This is the Alembic Config object
config = context.config

# Target metadata for autogenerate
target_metadata = Base.metadata

# Advisory lock ID to prevent concurrent migrations
MIGRATION_LOCK_ID = 1


def get_database_url() -> str:
    """Get database URL from environment, falling back to alembic.ini."""
    url = os.getenv("DATABASE_URL")
    if url:
        return url
    return config.get_main_option("sqlalchemy.url", "")


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    Configures the context with just a URL and not an Engine.
    Calls to context.execute() emit the given string to the script output.
    """
    url = get_database_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: object) -> None:
    """Run migrations within an advisory lock.

    Acquires pg_advisory_lock(1) before running migrations to prevent
    concurrent migration corruption when multiple services start simultaneously.
    The outer async context (begin()) handles the transaction commit.
    """
    # Acquire advisory lock to prevent concurrent migrations
    connection.execute(text(f"SELECT pg_advisory_lock({MIGRATION_LOCK_ID})"))  # type: ignore[union-attr]

    try:
        context.configure(
            connection=connection,  # type: ignore[arg-type]
            target_metadata=target_metadata,
            compare_type=True,
            transaction_per_migration=False,
        )

        context.run_migrations()
    finally:
        # Release advisory lock
        connection.execute(text(f"SELECT pg_advisory_unlock({MIGRATION_LOCK_ID})"))  # type: ignore[union-attr]


async def run_async_migrations() -> None:
    """Create async engine and run migrations.

    Uses NullPool for migrations since we only need a single connection
    and don't want pool overhead during schema changes.
    Uses begin() to ensure migration DDL is committed.
    """
    url = get_database_url()
    connectable = create_async_engine(url, poolclass=pool.NullPool)

    async with connectable.begin() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode using async engine."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
