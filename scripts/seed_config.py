"""Seed system_config with operational defaults.

All values are stored as raw Python primitives in JSONB (e.g., True not {"enabled": True}).
Uses ON CONFLICT DO UPDATE for idempotent upserts.

Usage:
    python -m scripts.seed_config
"""

import asyncio
from datetime import UTC, datetime

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from thinktank.models.config_table import SystemConfig

CONFIG_DEFAULTS = [
    {"key": "workers_active", "value": False},
    {"key": "max_candidates_per_day", "value": 20},
    {"key": "llm_timeout_hours", "value": 2},
    {"key": "backpressure_threshold", "value": 100},
    {"key": "gpu_idle_timeout_minutes", "value": 30},
    {"key": "gpu_queue_threshold", "value": 5},
    {"key": "discovery_priority_default", "value": 5},
    {"key": "min_duration_seconds", "value": 600},
    {"key": "reclaim_interval_seconds", "value": 300},
    {"key": "stale_job_threshold_minutes", "value": 30},
]


async def seed_config(session: AsyncSession) -> int:
    """Seed all system_config defaults into the database.

    Uses ON CONFLICT DO UPDATE for idempotent upserts.
    Values are stored as raw primitives in JSONB.
    Returns the number of config entries seeded.
    """
    now = datetime.now(UTC).replace(tzinfo=None)
    count = 0

    for entry in CONFIG_DEFAULTS:
        stmt = (
            insert(SystemConfig)
            .values(
                key=entry["key"],
                value=entry["value"],
                set_by="seed",
                updated_at=now,
            )
            .on_conflict_do_update(
                index_elements=["key"],
                set_={"value": entry["value"], "set_by": "seed", "updated_at": now},
            )
        )
        await session.execute(stmt)
        count += 1

    return count


if __name__ == "__main__":

    async def _main() -> None:
        from thinktank.database import async_session_factory

        async with async_session_factory() as session:
            count = await seed_config(session)
            await session.commit()
            print(f"Seeded {count} config entries")

    asyncio.run(_main())
