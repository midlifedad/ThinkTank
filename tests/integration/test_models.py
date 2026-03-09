"""Integration tests for all 14 model types against real PostgreSQL.

Tests verify:
1. Every model can be persisted via factory create_ function and read back
2. Relationship traversals work correctly
3. Foreign key constraints are enforced
4. Unique constraints are enforced
"""

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.thinktank.models import (
    ApiUsage,
    CandidateThinker,
    Category,
    Content,
    ContentThinker,
    Job,
    LLMReview,
    RateLimitUsage,
    Source,
    SystemConfig,
    Thinker,
    ThinkerCategory,
    ThinkerMetrics,
    ThinkerProfile,
)
from tests.factories import (
    create_api_usage,
    create_candidate_thinker,
    create_category,
    create_content,
    create_content_thinker,
    create_job,
    create_llm_review,
    create_rate_limit_usage,
    create_source,
    create_system_config,
    create_thinker,
    create_thinker_category,
    create_thinker_metrics,
    create_thinker_profile,
)


# ---------- Category ----------


@pytest.mark.asyncio
async def test_create_category(session: AsyncSession):
    """Category can be created, persisted, and read back with correct fields."""
    cat = await create_category(session, slug="test-cat", name="Test Category")
    result = await session.get(Category, cat.id)
    assert result is not None
    assert result.slug == "test-cat"
    assert result.name == "Test Category"


# ---------- Thinker ----------


@pytest.mark.asyncio
async def test_create_thinker(session: AsyncSession):
    """Thinker can be created and all fields persist correctly."""
    thinker = await create_thinker(session, name="Ada Lovelace", slug="ada-lovelace", tier=1)
    result = await session.get(Thinker, thinker.id)
    assert result is not None
    assert result.name == "Ada Lovelace"
    assert result.slug == "ada-lovelace"
    assert result.tier == 1
    assert result.active is True
    assert result.approval_status == "approved"


# ---------- ThinkerCategory ----------


@pytest.mark.asyncio
async def test_create_thinker_with_category(session: AsyncSession):
    """Thinker and Category can be linked via ThinkerCategory junction."""
    thinker = await create_thinker(session)
    cat = await create_category(session)
    tc = await create_thinker_category(
        session, thinker_id=thinker.id, category_id=cat.id, relevance=8
    )
    assert tc.relevance == 8

    # Verify the junction entry exists
    result = await session.get(ThinkerCategory, (thinker.id, cat.id))
    assert result is not None


# ---------- Source ----------


@pytest.mark.asyncio
async def test_create_source_for_thinker(session: AsyncSession):
    """Source can be created with a thinker FK and the relationship works."""
    thinker = await create_thinker(session)
    source = await create_source(session, thinker_id=thinker.id, name="Test Podcast")
    result = await session.get(Source, source.id)
    assert result is not None
    assert result.thinker_id == thinker.id
    assert result.name == "Test Podcast"


# ---------- Content ----------


@pytest.mark.asyncio
async def test_create_content_with_source(session: AsyncSession):
    """Content can be created through the thinker->source->content chain."""
    thinker = await create_thinker(session)
    source = await create_source(session, thinker_id=thinker.id)
    content = await create_content(
        session,
        source_id=source.id,
        source_owner_id=thinker.id,
        title="Great Episode",
    )
    result = await session.get(Content, content.id)
    assert result is not None
    assert result.source_id == source.id
    assert result.source_owner_id == thinker.id
    assert result.title == "Great Episode"


# ---------- ContentThinker ----------


@pytest.mark.asyncio
async def test_create_content_thinker(session: AsyncSession):
    """ContentThinker junction links content to thinker with role and confidence."""
    thinker = await create_thinker(session)
    source = await create_source(session, thinker_id=thinker.id)
    content = await create_content(session, source_id=source.id, source_owner_id=thinker.id)
    ct = await create_content_thinker(
        session, content_id=content.id, thinker_id=thinker.id, role="primary", confidence=10
    )
    result = await session.get(ContentThinker, (content.id, thinker.id))
    assert result is not None
    assert result.role == "primary"
    assert result.confidence == 10


# ---------- CandidateThinker ----------


@pytest.mark.asyncio
async def test_create_candidate_thinker(session: AsyncSession):
    """CandidateThinker persists with correct defaults (status=pending_llm)."""
    ct = await create_candidate_thinker(session, name="John Doe")
    result = await session.get(CandidateThinker, ct.id)
    assert result is not None
    assert result.name == "John Doe"
    assert result.status == "pending_llm"


# ---------- Job ----------


@pytest.mark.asyncio
async def test_create_job(session: AsyncSession):
    """Job persists with correct defaults (status=pending, attempts=0)."""
    job = await create_job(session, job_type="fetch_podcast_feed")
    result = await session.get(Job, job.id)
    assert result is not None
    assert result.status == "pending"
    assert result.attempts == 0
    assert result.job_type == "fetch_podcast_feed"


# ---------- LLMReview ----------


@pytest.mark.asyncio
async def test_create_llm_review(session: AsyncSession):
    """LLMReview persists with context_snapshot JSONB."""
    snapshot = {"queue_depth": 42, "thinkers_pending": 5}
    review = await create_llm_review(
        session,
        context_snapshot=snapshot,
        decision="approved",
        decision_reasoning="Looks good",
    )
    result = await session.get(LLMReview, review.id)
    assert result is not None
    assert result.context_snapshot == snapshot
    assert result.decision == "approved"


# ---------- SystemConfig ----------


@pytest.mark.asyncio
async def test_create_system_config(session: AsyncSession):
    """SystemConfig persists with TEXT PK and JSONB value."""
    sc = await create_system_config(
        session, key="workers_active", value=True, set_by="admin"
    )
    result = await session.get(SystemConfig, "workers_active")
    assert result is not None
    assert result.value is True
    assert result.set_by == "admin"


# ---------- RateLimitUsage ----------


@pytest.mark.asyncio
async def test_create_rate_limit_usage(session: AsyncSession):
    """RateLimitUsage persists with timestamp."""
    rl = await create_rate_limit_usage(session, api_name="listennotes")
    result = await session.get(RateLimitUsage, rl.id)
    assert result is not None
    assert result.api_name == "listennotes"
    assert result.called_at is not None


# ---------- ApiUsage ----------


@pytest.mark.asyncio
async def test_create_api_usage(session: AsyncSession):
    """ApiUsage persists with numeric fields."""
    au = await create_api_usage(session, api_name="youtube", call_count=10)
    result = await session.get(ApiUsage, au.id)
    assert result is not None
    assert result.api_name == "youtube"
    assert result.call_count == 10


# ---------- Unique constraint tests ----------


@pytest.mark.asyncio
async def test_thinker_slug_unique_constraint(session: AsyncSession):
    """Two thinkers with the same slug should raise IntegrityError."""
    await create_thinker(session, slug="unique-slug")
    with pytest.raises(IntegrityError):
        await create_thinker(session, slug="unique-slug")


@pytest.mark.asyncio
async def test_content_canonical_url_unique(session: AsyncSession):
    """Two content items with the same canonical_url should raise IntegrityError."""
    thinker = await create_thinker(session)
    source = await create_source(session, thinker_id=thinker.id)
    await create_content(
        session,
        source_id=source.id,
        source_owner_id=thinker.id,
        canonical_url="https://example.com/same",
    )
    with pytest.raises(IntegrityError):
        await create_content(
            session,
            source_id=source.id,
            source_owner_id=thinker.id,
            canonical_url="https://example.com/same",
        )


# ---------- FK constraint tests ----------


@pytest.mark.asyncio
async def test_source_fk_constraint(session: AsyncSession):
    """Content with nonexistent source_id should raise IntegrityError."""
    thinker = await create_thinker(session)
    with pytest.raises(IntegrityError):
        await create_content(
            session,
            source_id=uuid.uuid4(),  # nonexistent
            source_owner_id=thinker.id,
        )


# ---------- Relationship tests ----------


@pytest.mark.asyncio
async def test_thinker_profile_relationship(session: AsyncSession):
    """Thinker with a profile -- can access profile via relationship."""
    thinker = await create_thinker(session)
    profile = await create_thinker_profile(
        session,
        thinker_id=thinker.id,
        education=[{"school": "MIT", "degree": "PhD"}],
    )
    result = await session.get(ThinkerProfile, profile.id)
    assert result is not None
    assert result.thinker_id == thinker.id
    assert result.education == [{"school": "MIT", "degree": "PhD"}]


@pytest.mark.asyncio
async def test_thinker_metrics_relationship(session: AsyncSession):
    """Thinker with metrics -- metrics persist correctly."""
    thinker = await create_thinker(session)
    metrics = await create_thinker_metrics(
        session,
        thinker_id=thinker.id,
        platform="twitter",
        followers=50000,
    )
    result = await session.get(ThinkerMetrics, metrics.id)
    assert result is not None
    assert result.thinker_id == thinker.id
    assert result.platform == "twitter"
    assert result.followers == 50000
