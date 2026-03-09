"""Integration tests for bootstrap seed scripts and orchestrator.

Tests verify:
- Category taxonomy seeding with parent/child relationships
- System config defaults seeding
- Thinker seeding with LLM approval jobs
- Full bootstrap sequence
- Idempotency of all seed operations
"""

import uuid

import pytest
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from src.thinktank.models.category import Category
from src.thinktank.models.config_table import SystemConfig
from src.thinktank.models.job import Job
from src.thinktank.models.thinker import Thinker

pytestmark = pytest.mark.anyio


class TestSeedCategories:
    """seed_categories inserts a category taxonomy with parent/child relationships."""

    async def test_seed_categories_creates_hierarchy(self, session: AsyncSession):
        """Running seed_categories creates categories with correct parent/child relationships."""
        from scripts.seed_categories import seed_categories

        count = await seed_categories(session)
        await session.commit()

        # Should have created multiple categories
        assert count > 0

        # Verify top-level categories exist (no parent_id)
        result = await session.execute(
            select(Category).where(Category.parent_id.is_(None))
        )
        top_level = result.scalars().all()
        assert len(top_level) >= 3, "Expected at least 3 top-level categories"

        # Verify subcategories exist (have parent_id)
        result = await session.execute(
            select(Category).where(Category.parent_id.is_not(None))
        )
        children = result.scalars().all()
        assert len(children) > 0, "Expected subcategories with parent references"

        # Verify parent references are valid
        for child in children:
            parent_result = await session.execute(
                select(Category).where(Category.id == child.parent_id)
            )
            parent = parent_result.scalar_one_or_none()
            assert parent is not None, f"Child {child.slug} has invalid parent_id"

    async def test_seed_categories_idempotent(self, session: AsyncSession):
        """Running seed_categories twice produces no errors and same count."""
        from scripts.seed_categories import seed_categories

        count1 = await seed_categories(session)
        await session.commit()

        count2 = await seed_categories(session)
        await session.commit()

        assert count1 == count2

        # Verify no duplicates
        result = await session.execute(select(func.count()).select_from(Category))
        total = result.scalar()
        assert total == count1

    async def test_seed_categories_uses_deterministic_ids(self, session: AsyncSession):
        """Category IDs are deterministic based on slug using uuid5."""
        from scripts.seed_categories import seed_categories

        await seed_categories(session)
        await session.commit()

        # Get a known category
        result = await session.execute(
            select(Category).where(Category.slug == "technology")
        )
        cat = result.scalar_one()

        expected_id = uuid.uuid5(uuid.NAMESPACE_DNS, "thinktank.category.technology")
        assert cat.id == expected_id


class TestSeedConfig:
    """seed_config inserts all system_config defaults."""

    async def test_seed_config_creates_defaults(self, session: AsyncSession):
        """Running seed_config creates all expected config keys with correct values."""
        from scripts.seed_config import seed_config, CONFIG_DEFAULTS

        count = await seed_config(session)
        await session.commit()

        assert count == len(CONFIG_DEFAULTS)

        # Verify each key exists with the correct value
        for entry in CONFIG_DEFAULTS:
            result = await session.execute(
                select(SystemConfig).where(SystemConfig.key == entry["key"])
            )
            config = result.scalar_one()
            assert config.value == entry["value"], (
                f"Config {entry['key']}: expected {entry['value']}, got {config.value}"
            )
            assert config.set_by == "seed"

    async def test_seed_config_idempotent(self, session: AsyncSession):
        """Running seed_config twice produces no errors."""
        from scripts.seed_config import seed_config

        count1 = await seed_config(session)
        await session.commit()

        count2 = await seed_config(session)
        await session.commit()

        assert count1 == count2

    async def test_seed_config_workers_active_false(self, session: AsyncSession):
        """workers_active defaults to False (bootstrap activates later)."""
        from scripts.seed_config import seed_config

        await seed_config(session)
        await session.commit()

        result = await session.execute(
            select(SystemConfig).where(SystemConfig.key == "workers_active")
        )
        config = result.scalar_one()
        assert config.value is False


class TestSeedThinkers:
    """seed_thinkers inserts initial thinkers with LLM approval jobs."""

    async def test_seed_thinkers_creates_with_llm_jobs(self, session: AsyncSession):
        """Seeding thinkers creates 5 thinkers with pending_llm status and LLM jobs."""
        from scripts.seed_categories import seed_categories
        from scripts.seed_thinkers import seed_thinkers, INITIAL_THINKERS

        # Categories must exist first (FK requirements for thinker_categories)
        await seed_categories(session)
        await session.commit()

        count = await seed_thinkers(session)
        await session.commit()

        assert count == len(INITIAL_THINKERS)

        # Verify thinkers exist with correct status
        result = await session.execute(select(Thinker))
        thinkers = result.scalars().all()
        assert len(thinkers) == len(INITIAL_THINKERS)

        for t in thinkers:
            assert t.approval_status == "pending_llm"

        # Verify LLM approval jobs created
        result = await session.execute(
            select(Job).where(Job.job_type == "llm_approval_check")
        )
        jobs = result.scalars().all()
        assert len(jobs) == len(INITIAL_THINKERS)

        # Verify each job references a thinker
        thinker_ids = {str(t.id) for t in thinkers}
        for job in jobs:
            assert job.payload["review_type"] == "thinker_approval"
            assert job.payload["thinker_id"] in thinker_ids

    async def test_seed_thinkers_idempotent(self, session: AsyncSession):
        """Running seed_thinkers twice produces no duplicate thinkers or jobs."""
        from scripts.seed_categories import seed_categories
        from scripts.seed_thinkers import seed_thinkers, INITIAL_THINKERS

        await seed_categories(session)
        await session.commit()

        count1 = await seed_thinkers(session)
        await session.commit()

        count2 = await seed_thinkers(session)
        await session.commit()

        assert count1 == count2

        # No duplicate thinkers
        result = await session.execute(select(func.count()).select_from(Thinker))
        total_thinkers = result.scalar()
        assert total_thinkers == len(INITIAL_THINKERS)

        # No duplicate jobs
        result = await session.execute(
            select(func.count()).select_from(Job).where(
                Job.job_type == "llm_approval_check"
            )
        )
        total_jobs = result.scalar()
        assert total_jobs == len(INITIAL_THINKERS)


class TestBootstrap:
    """bootstrap orchestrates the full seed sequence."""

    async def test_bootstrap_full_sequence(self, session: AsyncSession):
        """Running bootstrap on empty DB seeds categories, config, thinkers and activates workers."""
        from scripts.bootstrap import bootstrap

        results = await bootstrap(session)
        await session.commit()

        # Verify counts returned
        assert results["categories"] > 0
        assert results["config"] > 0
        assert results["thinkers"] > 0

        # Verify workers_active is True after bootstrap
        result = await session.execute(
            select(SystemConfig).where(SystemConfig.key == "workers_active")
        )
        config = result.scalar_one()
        assert config.value is True

        # Verify categories exist
        result = await session.execute(select(func.count()).select_from(Category))
        assert result.scalar() > 0

        # Verify thinkers exist
        result = await session.execute(select(func.count()).select_from(Thinker))
        assert result.scalar() > 0

        # Verify LLM jobs exist
        result = await session.execute(
            select(func.count()).select_from(Job).where(
                Job.job_type == "llm_approval_check"
            )
        )
        assert result.scalar() > 0

    async def test_bootstrap_validates_schema(self, session: AsyncSession):
        """Bootstrap validates that tables exist before proceeding."""
        from scripts.bootstrap import bootstrap

        # On a working DB with tables, this should succeed
        results = await bootstrap(session)
        await session.commit()

        assert "categories" in results
        assert "config" in results
        assert "thinkers" in results
