"""Integration tests for FK ON DELETE CASCADE / SET NULL behavior.

Source: DATA-REVIEW C2 -- deleting a Thinker currently raises FK violations
because junction tables have no ondelete behavior defined. These tests pin
down the expected cascade / set-null behavior added in migration 005.
"""

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.factories import (
    create_candidate_thinker,
    create_category,
    create_content,
    create_content_thinker,
    create_source,
    create_source_category,
    create_source_thinker,
    create_thinker,
    create_thinker_category,
    create_thinker_metrics,
    create_thinker_profile,
)
from thinktank.models import (
    CandidateThinker,
    Category,
    ContentThinker,
    SourceCategory,
    SourceThinker,
    ThinkerCategory,
    ThinkerMetrics,
    ThinkerProfile,
)


@pytest.mark.asyncio
async def test_deleting_thinker_cascades_content_thinkers(session: AsyncSession):
    """Deleting a Thinker CASCADEs to content_thinkers junction rows."""
    thinker = await create_thinker(session)
    source = await create_source(session)
    content = await create_content(session, source_id=source.id)
    await create_content_thinker(session, content_id=content.id, thinker_id=thinker.id)
    await session.commit()

    # Re-fetch to avoid stale identity-map references when we delete.
    thinker_id = thinker.id
    await session.delete(await session.get(type(thinker), thinker_id))
    await session.commit()

    result = await session.execute(select(ContentThinker).where(ContentThinker.thinker_id == thinker_id))
    assert result.scalars().all() == []


@pytest.mark.asyncio
async def test_deleting_thinker_cascades_source_thinkers(session: AsyncSession):
    """Deleting a Thinker CASCADEs to source_thinkers junction rows."""
    thinker = await create_thinker(session)
    source = await create_source(session)
    await create_source_thinker(session, source_id=source.id, thinker_id=thinker.id)
    await session.commit()

    thinker_id = thinker.id
    await session.delete(await session.get(type(thinker), thinker_id))
    await session.commit()

    result = await session.execute(select(SourceThinker).where(SourceThinker.thinker_id == thinker_id))
    assert result.scalars().all() == []


@pytest.mark.asyncio
async def test_deleting_thinker_cascades_thinker_profiles(session: AsyncSession):
    """Deleting a Thinker CASCADEs to thinker_profiles rows."""
    thinker = await create_thinker(session)
    profile = await create_thinker_profile(session, thinker_id=thinker.id)
    await session.commit()

    profile_id = profile.id
    thinker_id = thinker.id
    await session.delete(await session.get(type(thinker), thinker_id))
    await session.commit()

    # Expire the identity map so session.get re-fetches from the DB
    # rather than returning the cached (now-orphaned) instance.
    session.expire_all()
    result = await session.execute(select(ThinkerProfile).where(ThinkerProfile.id == profile_id))
    assert result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_deleting_thinker_cascades_thinker_metrics(session: AsyncSession):
    """Deleting a Thinker CASCADEs to thinker_metrics rows."""
    thinker = await create_thinker(session)
    metrics = await create_thinker_metrics(session, thinker_id=thinker.id)
    await session.commit()

    metrics_id = metrics.id
    thinker_id = thinker.id
    await session.delete(await session.get(type(thinker), thinker_id))
    await session.commit()

    session.expire_all()
    result = await session.execute(select(ThinkerMetrics).where(ThinkerMetrics.id == metrics_id))
    assert result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_deleting_thinker_cascades_thinker_categories(session: AsyncSession):
    """Deleting a Thinker CASCADEs to thinker_categories junction rows."""
    thinker = await create_thinker(session)
    category = await create_category(session)
    await create_thinker_category(session, thinker_id=thinker.id, category_id=category.id)
    await session.commit()

    thinker_id = thinker.id
    await session.delete(await session.get(type(thinker), thinker_id))
    await session.commit()

    result = await session.execute(select(ThinkerCategory).where(ThinkerCategory.thinker_id == thinker_id))
    assert result.scalars().all() == []


@pytest.mark.asyncio
async def test_deleting_source_cascades_source_categories(session: AsyncSession):
    """Deleting a Source CASCADEs to source_categories junction rows."""
    source = await create_source(session)
    category = await create_category(session)
    await create_source_category(session, source_id=source.id, category_id=category.id)
    await session.commit()

    source_id = source.id
    await session.delete(await session.get(type(source), source_id))
    await session.commit()

    result = await session.execute(select(SourceCategory).where(SourceCategory.source_id == source_id))
    assert result.scalars().all() == []


@pytest.mark.asyncio
async def test_deleting_category_cascades_source_categories(session: AsyncSession):
    """Deleting a Category CASCADEs to source_categories rows (side FK)."""
    source = await create_source(session)
    category = await create_category(session)
    await create_source_category(session, source_id=source.id, category_id=category.id)
    await session.commit()

    category_id = category.id
    await session.delete(await session.get(Category, category_id))
    await session.commit()

    result = await session.execute(select(SourceCategory).where(SourceCategory.category_id == category_id))
    assert result.scalars().all() == []


@pytest.mark.asyncio
async def test_deleting_thinker_sets_candidate_thinker_id_to_null(session: AsyncSession):
    """Deleting a promoted Thinker SETs NULL on candidate.thinker_id (history preserved)."""
    thinker = await create_thinker(session)
    candidate = await create_candidate_thinker(session, thinker_id=thinker.id, status="promoted")
    await session.commit()

    candidate_id = candidate.id
    thinker_id = thinker.id
    await session.delete(await session.get(type(thinker), thinker_id))
    await session.commit()

    # Candidate row must still exist (history); thinker_id must be NULL.
    await session.refresh(await session.get(CandidateThinker, candidate_id))
    persisted = await session.get(CandidateThinker, candidate_id)
    assert persisted is not None
    assert persisted.thinker_id is None


@pytest.mark.asyncio
async def test_deleting_content_cascades_content_thinkers(session: AsyncSession):
    """Deleting Content CASCADEs to content_thinkers junction rows."""
    thinker = await create_thinker(session)
    source = await create_source(session)
    content = await create_content(session, source_id=source.id)
    await create_content_thinker(session, content_id=content.id, thinker_id=thinker.id)
    await session.commit()

    content_id = content.id
    await session.delete(await session.get(type(content), content_id))
    await session.commit()

    result = await session.execute(select(ContentThinker).where(ContentThinker.content_id == content_id))
    assert result.scalars().all() == []


@pytest.mark.asyncio
async def test_deleting_source_cascades_source_thinkers(session: AsyncSession):
    """Deleting a Source CASCADEs to source_thinkers junction rows."""
    source = await create_source(session)
    thinker = await create_thinker(session)
    await create_source_thinker(session, source_id=source.id, thinker_id=thinker.id)
    await session.commit()

    source_id = source.id
    await session.delete(await session.get(type(source), source_id))
    await session.commit()

    result = await session.execute(select(SourceThinker).where(SourceThinker.source_id == source_id))
    assert result.scalars().all() == []


# Silence unused-import lint warnings (uuid imported for type checker ergonomics)
_ = uuid
