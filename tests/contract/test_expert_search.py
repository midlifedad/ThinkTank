"""Contract tests for the expert_search seed handler.

Contract:
    - Given (mocked) Perplexity + OpenAlex seed lanes
    - When expert_search runs for an area
    - Then new candidates are created with provenance/hints and one
      vet_candidate job each; names matching existing thinkers or open
      candidates are skipped; lane overlap merges to seed_source="both"
"""

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.factories import create_candidate_thinker, create_job, create_thinker
from thinktank.handlers.expert_search import handle_expert_search
from thinktank.models.candidate import CandidateThinker
from thinktank.models.job import Job

pytestmark = pytest.mark.anyio

PPLX = [
    {
        "name": "Alice Sage",
        "basis": "Wrote the defining textbook",
        "affiliation": "MIT",
        "youtube_url": "https://youtube.com/@alicesage",
        "notable_podcasts": ["Big Ideas"],
    },
    {"name": "Bob Cited", "basis": "Field pioneer", "affiliation": "Oxford"},
]
OPENALEX = [
    {"name": "Bob Cited", "basis": "OpenAlex top-cited in AI safety (90,000 citations)", "affiliation": "Oxford"},
    {"name": "Carol Scholar", "basis": "OpenAlex top-cited in AI safety (40,000 citations)", "affiliation": "ETH"},
]


async def _run(session: AsyncSession, area="AI safety") -> None:
    job = await create_job(session, job_type="expert_search", payload={"area": area})
    with (
        patch(
            "thinktank.handlers.expert_search.search_experts",
            new_callable=AsyncMock,
            return_value=[dict(c) for c in PPLX],
        ),
        patch(
            "thinktank.handlers.expert_search.seed_from_openalex",
            new_callable=AsyncMock,
            return_value=[dict(c) for c in OPENALEX],
        ),
    ):
        await handle_expert_search(session, job)


async def _candidates(session: AsyncSession) -> dict[str, CandidateThinker]:
    result = await session.execute(select(CandidateThinker).where(CandidateThinker.search_area.is_not(None)))
    return {c.name: c for c in result.scalars().all()}


class TestSeeding:
    async def test_creates_candidates_with_provenance_and_vet_jobs(self, session: AsyncSession):
        await _run(session)

        cands = await _candidates(session)
        assert set(cands) == {"Alice Sage", "Bob Cited", "Carol Scholar"}

        alice = cands["Alice Sage"]
        assert alice.status == "vetting"
        assert alice.seed_source == "perplexity"
        assert alice.search_area == "AI safety"
        assert alice.evidence["hints"]["youtube_url"] == "https://youtube.com/@alicesage"
        assert alice.evidence["seed_claim"]["affiliation"] == "MIT"
        assert alice.inferred_categories == ["AI safety"]

        # Lane overlap -> both
        assert cands["Bob Cited"].seed_source == "both"
        assert cands["Carol Scholar"].seed_source == "openalex"

        vet_jobs = (await session.execute(select(Job).where(Job.job_type == "vet_candidate"))).scalars().all()
        assert {j.payload["candidate_id"] for j in vet_jobs} == {str(c.id) for c in cands.values()}

    async def test_existing_thinker_skipped(self, session: AsyncSession):
        await create_thinker(session, name="Alice Sage")
        await _run(session)

        cands = await _candidates(session)
        assert "Alice Sage" not in cands
        assert set(cands) == {"Bob Cited", "Carol Scholar"}

    async def test_existing_candidate_skipped(self, session: AsyncSession):
        await create_candidate_thinker(session, name="Bob Cited", normalized_name="bob cited")
        await _run(session)

        cands = await _candidates(session)
        assert "Bob Cited" not in cands

    async def test_missing_area_raises(self, session: AsyncSession):
        job = await create_job(session, job_type="expert_search", payload={})
        with pytest.raises(ValueError, match="area missing"):
            await handle_expert_search(session, job)
