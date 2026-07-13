"""Live-pgvector tests for canonical claim resolution threshold bands.

The bands are the whole design: attach without LLM when the embedding
signal is unambiguous, adjudicate only in the gray zone, create new
otherwise. Each band is exercised against real cosine ANN.
"""

import math
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.factories import create_claim
from thinktank.discovery.claim_resolution import resolve_claim
from thinktank.models.claim import Claim

pytestmark = pytest.mark.anyio


def _basis(i: int) -> list[float]:
    v = [0.0] * 768
    v[i] = 1.0
    return v


def _blend(similarity: float) -> list[float]:
    """Unit vector with exactly `similarity` cosine to _basis(0)."""
    v = [0.0] * 768
    v[0] = similarity
    v[1] = math.sqrt(1.0 - similarity**2)
    return v


ASSERTED = datetime(2026, 3, 1, tzinfo=UTC)


class TestResolveClaim:
    async def test_high_similarity_attaches_without_llm(self, session: AsyncSession):
        existing = await create_claim(session, proposition="Rapamycin extends lifespan", embedding=_basis(0))
        with patch("thinktank.discovery.claim_resolution._same_proposition", new=AsyncMock()) as judge:
            resolved = await resolve_claim(
                session,
                claim_text="Rapamycin extends lifespan in mammals",
                claim_type="factual",
                embedding=_blend(0.95),
                parent_claim_id=None,
                asserted_at=ASSERTED,
            )
        assert resolved.id == existing.id
        judge.assert_not_awaited()
        assert resolved.observation_count == 1
        assert resolved.first_observed_at == ASSERTED
        assert resolved.last_observed_at == ASSERTED

    async def test_low_similarity_creates_new_with_parent(self, session: AsyncSession):
        await create_claim(session, proposition="Rapamycin extends lifespan", embedding=_basis(0))
        headline = await create_claim(session, proposition="headline", embedding=_basis(10))
        with patch("thinktank.discovery.claim_resolution._same_proposition", new=AsyncMock()) as judge:
            resolved = await resolve_claim(
                session,
                claim_text="Metformin lowers blood glucose",
                claim_type="factual",
                embedding=_basis(5),
                parent_claim_id=headline.id,
                asserted_at=ASSERTED,
            )
        await session.commit()
        judge.assert_not_awaited()
        assert resolved.proposition == "Metformin lowers blood glucose"
        assert resolved.parent_claim_id == headline.id
        count = len((await session.execute(select(Claim))).scalars().all())
        assert count == 3

    async def test_ambiguity_band_attaches_when_judge_agrees(self, session: AsyncSession):
        existing = await create_claim(session, proposition="Rapamycin extends lifespan", embedding=_basis(0))
        with patch("thinktank.discovery.claim_resolution._same_proposition", new=AsyncMock(return_value=True)) as judge:
            resolved = await resolve_claim(
                session,
                claim_text="Rapamycin makes organisms live longer",
                claim_type="factual",
                embedding=_blend(0.85),
                parent_claim_id=None,
                asserted_at=ASSERTED,
            )
        judge.assert_awaited_once()
        assert resolved.id == existing.id

    async def test_ambiguity_band_creates_new_when_judge_disagrees(self, session: AsyncSession):
        existing = await create_claim(session, proposition="Rapamycin extends lifespan", embedding=_basis(0))
        with patch(
            "thinktank.discovery.claim_resolution._same_proposition", new=AsyncMock(return_value=False)
        ) as judge:
            resolved = await resolve_claim(
                session,
                claim_text="Rapamycin extends lifespan ONLY in male mice",
                claim_type="factual",
                embedding=_blend(0.85),
                parent_claim_id=None,
                asserted_at=ASSERTED,
            )
        judge.assert_awaited_once()
        assert resolved.id != existing.id

    async def test_merged_and_inactive_claims_excluded(self, session: AsyncSession):
        survivor = await create_claim(session, proposition="survivor", embedding=_basis(3))
        await create_claim(session, proposition="merged away", embedding=_basis(0), merged_into_id=survivor.id)
        resolved = await resolve_claim(
            session,
            claim_text="fresh proposition",
            claim_type="opinion",
            embedding=_basis(0),
            parent_claim_id=None,
            asserted_at=ASSERTED,
        )
        # Identical embedding to the merged claim, but merged claims are
        # not attach targets -- a new canonical claim is created.
        assert resolved.proposition == "fresh proposition"

    async def test_timestamps_widen_not_shrink(self, session: AsyncSession):
        earlier = datetime(2025, 1, 1, tzinfo=UTC)
        later = datetime(2026, 6, 1, tzinfo=UTC)
        await create_claim(session, proposition="anchor", embedding=_basis(0))
        first = await resolve_claim(
            session,
            claim_text="x",
            claim_type="factual",
            embedding=_blend(0.95),
            parent_claim_id=None,
            asserted_at=later,
        )
        second = await resolve_claim(
            session,
            claim_text="x",
            claim_type="factual",
            embedding=_blend(0.95),
            parent_claim_id=None,
            asserted_at=earlier,
        )
        assert first.id == second.id
        assert second.first_observed_at == earlier
        assert second.last_observed_at == later
        assert second.observation_count == 2
