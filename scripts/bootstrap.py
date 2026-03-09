"""Bootstrap orchestrator for fresh ThinkTank deployments.

Runs all seed scripts in order, validates prerequisites between steps,
and activates workers at the end. Produces a fully operational system
from an empty (but schema-ready) database.

Usage:
    python -m scripts.bootstrap
"""

import asyncio

import structlog
from sqlalchemy import select, text, func
from sqlalchemy.ext.asyncio import AsyncSession

from scripts.seed_categories import seed_categories
from scripts.seed_config import seed_config
from scripts.seed_thinkers import seed_thinkers
from src.thinktank.models.category import Category
from src.thinktank.models.config_table import SystemConfig

logger = structlog.get_logger(__name__)


async def bootstrap(session: AsyncSession) -> dict[str, int]:
    """Run the full bootstrap sequence on a fresh database.

    Steps:
    1. Validate prerequisites (schema exists)
    2. Seed categories (taxonomy hierarchy)
    3. Seed config (operational defaults)
    4. Validate categories exist before seeding thinkers
    5. Seed thinkers (initial thinker list + LLM approval jobs)
    6. Activate workers (set workers_active=true)

    Returns dict with counts: {"categories": N, "config": N, "thinkers": N}
    Raises RuntimeError if prerequisites are not met.
    """
    # Step 1: Validate schema exists
    await logger.ainfo("bootstrap.validate_schema", step=1)
    try:
        await session.execute(text("SELECT 1 FROM categories LIMIT 1"))
    except Exception as exc:
        raise RuntimeError(
            "Schema not found. Run 'alembic upgrade head' first."
        ) from exc

    # Step 2: Seed categories
    await logger.ainfo("bootstrap.seed_categories", step=2)
    cat_count = await seed_categories(session)
    await logger.ainfo("bootstrap.seed_categories.done", count=cat_count)

    # Step 3: Seed config
    await logger.ainfo("bootstrap.seed_config", step=3)
    config_count = await seed_config(session)
    await logger.ainfo("bootstrap.seed_config.done", count=config_count)

    # Step 4: Validate categories exist before thinkers
    result = await session.execute(
        select(func.count()).select_from(Category)
    )
    category_total = result.scalar()
    if category_total == 0:
        raise RuntimeError(
            "Categories must exist before seeding thinkers. "
            "seed_categories failed silently."
        )

    # Step 5: Seed thinkers
    await logger.ainfo("bootstrap.seed_thinkers", step=5)
    thinker_count = await seed_thinkers(session)
    await logger.ainfo("bootstrap.seed_thinkers.done", count=thinker_count)

    # Step 6: Activate workers
    await logger.ainfo("bootstrap.activate_workers", step=6)
    stmt = (
        select(SystemConfig)
        .where(SystemConfig.key == "workers_active")
    )
    result = await session.execute(stmt)
    config = result.scalar_one()
    config.value = True
    await logger.ainfo("bootstrap.activate_workers.done", workers_active=True)

    results = {
        "categories": cat_count,
        "config": config_count,
        "thinkers": thinker_count,
    }
    await logger.ainfo("bootstrap.complete", results=results)
    return results


if __name__ == "__main__":

    async def _main() -> None:
        from src.thinktank.database import async_session_factory

        async with async_session_factory() as session:
            results = await bootstrap(session)
            await session.commit()
            print(f"Bootstrap complete: {results}")
            print(f"  Categories: {results['categories']}")
            print(f"  Config entries: {results['config']}")
            print(f"  Thinkers: {results['thinkers']}")
            print("  Workers: ACTIVE")

    asyncio.run(_main())
