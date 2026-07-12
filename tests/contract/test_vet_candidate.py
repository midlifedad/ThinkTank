"""Contract tests for the vet_candidate handler.

Contract:
    - Given a candidate and a (mocked) evidence dossier
    - When vet_candidate runs
    - Then the dossier/score persist and the candidate routes by gate
      outcome: auto_rejected (terminal, no LLM job), pending_human
      (borderline, no LLM job), or awaiting_llm (+ llm_approval_check
      job carrying candidate_ids)
"""

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.factories import create_candidate_thinker, create_job
from thinktank.handlers.vet_candidate import handle_vet_candidate
from thinktank.models.job import Job

pytestmark = pytest.mark.anyio


def _dossier(h_index=0, enwiki=False, sitelinks=0, books=0, podcast_feeds=0):
    return {
        "openalex": {"ok": True, "found": h_index > 0, "h_index": h_index, "cited_by_count": 0},
        "wikidata": {"ok": True, "found": enwiki, "has_enwiki": enwiki, "sitelink_count": sitelinks},
        "openlibrary": {"ok": True, "found": books > 0, "work_count": books},
        "podcastindex": {"ok": True, "found": podcast_feeds > 0, "appearance_feed_count": podcast_feeds},
        "youtube": {"ok": True, "checked": False},
        "substack": {"ok": True, "checked": False},
    }


async def _vet(session: AsyncSession, candidate, dossier) -> None:
    job = await create_job(session, job_type="vet_candidate", payload={"candidate_id": str(candidate.id)})
    with patch("thinktank.handlers.vet_candidate.gather_evidence", new_callable=AsyncMock, return_value=dossier):
        await handle_vet_candidate(session, job)


async def _llm_jobs(session: AsyncSession) -> list[Job]:
    result = await session.execute(select(Job).where(Job.job_type == "llm_approval_check"))
    return list(result.scalars().all())


class TestGateRouting:
    async def test_strong_candidate_shortlisted_and_enqueued(self, session: AsyncSession):
        candidate = await create_candidate_thinker(session, name="Eminent Expert", status="pending_llm")
        await _vet(session, candidate, _dossier(h_index=55, enwiki=True, sitelinks=30, books=5, podcast_feeds=10))

        await session.refresh(candidate)
        assert candidate.status == "awaiting_llm"
        assert candidate.qualification_score is not None and candidate.qualification_score >= 50
        assert candidate.evidence["openalex"]["h_index"] == 55
        jobs = await _llm_jobs(session)
        assert len(jobs) == 1
        assert jobs[0].payload == {
            "review_type": "candidate_review",
            "target_id": str(candidate.id),
            "candidate_ids": [str(candidate.id)],
        }

    async def test_unqualified_candidate_auto_rejected_no_llm(self, session: AsyncSession):
        """Content-only celebrity: terminal rejection, zero LLM spend."""
        candidate = await create_candidate_thinker(session, name="Big Podcaster", status="pending_llm")
        await _vet(session, candidate, _dossier(podcast_feeds=50))

        await session.refresh(candidate)
        assert candidate.status == "auto_rejected"
        assert candidate.reviewed_by == "vetting_gate"
        assert await _llm_jobs(session) == []

    async def test_borderline_routes_to_human_no_llm(self, session: AsyncSession):
        candidate = await create_candidate_thinker(session, name="Marginal Scholar", status="pending_llm")
        await _vet(session, candidate, _dossier(h_index=16, enwiki=True, podcast_feeds=3))

        await session.refresh(candidate)
        assert candidate.status == "pending_human"
        assert await _llm_jobs(session) == []

    async def test_already_reviewed_candidate_skipped(self, session: AsyncSession):
        candidate = await create_candidate_thinker(session, name="Done Deal", status="approved")
        await _vet(session, candidate, _dossier(h_index=55, enwiki=True, podcast_feeds=9))

        await session.refresh(candidate)
        assert candidate.status == "approved"
        assert candidate.qualification_score is None
