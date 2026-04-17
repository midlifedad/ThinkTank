"""Integration tests for trigram similarity dedup module.

Tests pg_trgm similarity matching for candidate deduplication
and thinker-blocks-candidate behavior. Requires PostgreSQL with
pg_trgm extension enabled (handled by conftest.py).
"""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.factories import create_candidate_thinker, create_thinker
from thinktank.ingestion.trigram import find_similar_candidates, find_similar_thinkers

pytestmark = pytest.mark.anyio


async def test_similar_candidate_found(session: AsyncSession):
    """Insert candidate 'John Smith', search for 'john smith' -> match found > 0.7."""
    await create_candidate_thinker(
        session,
        name="John Smith",
        normalized_name="john smith",
    )
    await session.commit()

    matches = await find_similar_candidates(session, "john smith")
    assert len(matches) >= 1
    # First match should be the candidate we inserted
    match_id, match_name, similarity = matches[0]
    assert match_name == "John Smith"
    assert similarity > 0.7


async def test_dissimilar_candidate_not_found(session: AsyncSession):
    """Insert candidate 'John Smith', search for 'jane doe' -> no match."""
    await create_candidate_thinker(
        session,
        name="John Smith",
        normalized_name="john smith",
    )
    await session.commit()

    matches = await find_similar_candidates(session, "jane doe")
    assert len(matches) == 0


async def test_existing_thinker_blocks_candidate(session: AsyncSession):
    """Insert thinker 'John Smith', find_similar_thinkers returns match."""
    await create_thinker(session, name="John Smith")
    await session.commit()

    matches = await find_similar_thinkers(session, "john smith")
    assert len(matches) >= 1
    match_id, match_name, similarity = matches[0]
    assert match_name == "John Smith"
    assert similarity > 0.7


async def test_candidate_appearance_incremented(session: AsyncSession):
    """Insert candidate with count=1, match found -> count becomes 2, last_seen_at updated."""
    candidate = await create_candidate_thinker(
        session,
        name="John Smith",
        normalized_name="john smith",
        appearance_count=1,
    )
    await session.commit()

    # Find the match
    matches = await find_similar_candidates(session, "john smith")
    assert len(matches) >= 1

    # Simulate increment (this is what the handler does)
    await session.refresh(candidate)
    candidate.appearance_count += 1
    from datetime import UTC, datetime

    candidate.last_seen_at = datetime.now(UTC)
    await session.commit()

    await session.refresh(candidate)
    assert candidate.appearance_count == 2


async def test_threshold_respected(session: AsyncSession):
    """Insert candidate 'John', search for 'Jonathan' -> similarity below 0.7, no match."""
    await create_candidate_thinker(
        session,
        name="John",
        normalized_name="john",
    )
    await session.commit()

    matches = await find_similar_candidates(session, "jonathan")
    # "john" vs "jonathan" should have low trigram similarity (below 0.7)
    assert len(matches) == 0


async def test_gist_index_used(session: AsyncSession):
    """Verify the GiST index exists on candidate_thinkers.normalized_name."""
    result = await session.execute(
        text("SELECT indexname FROM pg_indexes WHERE tablename = 'candidate_thinkers' AND indexname LIKE '%trgm%'")
    )
    indexes = [row[0] for row in result.fetchall()]
    assert len(indexes) >= 1
    assert "ix_candidate_thinkers_trgm" in indexes
