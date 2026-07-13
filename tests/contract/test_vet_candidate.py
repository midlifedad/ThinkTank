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


class TestPractitionerRouting:
    """Practitioner path routes to the judge with the evaluation flag."""

    async def _vet(self, session, candidate, dossier):
        from unittest.mock import AsyncMock, patch

        job = await create_job(session, job_type="vet_candidate", payload={"candidate_id": str(candidate.id)})
        with patch("thinktank.handlers.vet_candidate.gather_evidence", new_callable=AsyncMock, return_value=dossier):
            await handle_vet_candidate(session, job)

    async def test_practitioner_goes_to_judge_flagged(self, session):
        """Wikipedia + strong content, no scholarship -> awaiting_llm with
        evaluation_path=practitioner + an llm_approval_check job."""
        candidate = await create_candidate_thinker(
            session,
            name="Scott Marketer",
            normalized_name="scott marketer",
            status="pending_llm",
            search_area="AI-driven Marketing",
        )
        dossier = {
            "openalex": {"ok": True, "found": False},
            "wikidata": {"ok": True, "found": True, "has_enwiki": True, "sitelink_count": 6},
            "openlibrary": {"ok": True, "found": False},
            "podcastindex": {"ok": True, "found": True, "appearance_feed_count": 10},
            "youtube": {"ok": True, "checked": False},
            "substack": {"ok": True, "checked": False},
        }
        await self._vet(session, candidate, dossier)

        await session.refresh(candidate)
        assert candidate.status == "awaiting_llm"
        assert candidate.evidence["evaluation_path"] == "practitioner"
        jobs = await _llm_jobs(session)
        assert len(jobs) == 1


def _fit(centrality, score=18):
    return {"centrality": centrality, "fit_score": score, "reasoning": "test", "assessed_at": "2026-07-13T00:00:00Z"}


async def _vet_with_fit(session: AsyncSession, candidate, dossier, fit) -> None:
    job = await create_job(session, job_type="vet_candidate", payload={"candidate_id": str(candidate.id)})
    with (
        patch("thinktank.handlers.vet_candidate.gather_evidence", new_callable=AsyncMock, return_value=dossier),
        patch("thinktank.handlers.vet_candidate.assess_domain_fit", new_callable=AsyncMock, return_value=fit),
    ):
        await handle_vet_candidate(session, job)


class TestDomainFit:
    async def test_core_fit_rescues_weak_scorer_to_judge(self, session: AsyncSession):
        """The Weng/Nakajima fix: thin countable evidence + CORE fit -> judge."""
        candidate = await create_candidate_thinker(
            session, name="Area Creator", status="pending_llm", search_area="agentic engineering"
        )
        await _vet_with_fit(session, candidate, _dossier(podcast_feeds=10, sitelinks=4), _fit("core"))

        await session.refresh(candidate)
        assert candidate.status == "awaiting_llm"
        assert candidate.evidence["domain_fit"]["centrality"] == "core"
        assert candidate.evidence["evaluation_path"] == "fit_rescue"
        assert len(await _llm_jobs(session)) == 1

    async def test_peripheral_fit_stored_but_not_rescuing(self, session: AsyncSession):
        candidate = await create_candidate_thinker(
            session, name="Eminent Elsewhere", status="pending_llm", search_area="agentic engineering"
        )
        await _vet_with_fit(session, candidate, _dossier(podcast_feeds=4, sitelinks=2), _fit("peripheral", 3))

        await session.refresh(candidate)
        assert candidate.status == "auto_rejected"
        assert candidate.evidence["domain_fit"]["centrality"] == "peripheral"
        assert await _llm_jobs(session) == []

    async def test_fit_failure_leaves_gate_unchanged(self, session: AsyncSession):
        """assess_domain_fit returning None (fail-open) = pre-fit behavior."""
        candidate = await create_candidate_thinker(
            session, name="Fit Unavailable", status="pending_llm", search_area="agentic engineering"
        )
        await _vet_with_fit(session, candidate, _dossier(podcast_feeds=4, sitelinks=2), None)

        await session.refresh(candidate)
        assert candidate.status == "auto_rejected"
        assert "domain_fit" not in candidate.evidence

    async def test_no_area_skips_fit_call(self, session: AsyncSession):
        candidate = await create_candidate_thinker(session, name="No Area", status="pending_llm")
        job = await create_job(session, job_type="vet_candidate", payload={"candidate_id": str(candidate.id)})
        fit_mock = AsyncMock()
        with (
            patch(
                "thinktank.handlers.vet_candidate.gather_evidence",
                new_callable=AsyncMock,
                return_value=_dossier(h_index=55, enwiki=True, sitelinks=30, books=5, podcast_feeds=10),
            ),
            patch("thinktank.handlers.vet_candidate.assess_domain_fit", new=fit_mock),
        ):
            await handle_vet_candidate(session, job)
        fit_mock.assert_not_awaited()


class TestRosterCritiqueAutoEnqueue:
    async def test_last_vet_in_area_enqueues_critique_once(self, session: AsyncSession):
        area = "agentic engineering"
        candidate = await create_candidate_thinker(
            session, name="Only Candidate", status="pending_llm", search_area=area
        )
        await _vet_with_fit(session, candidate, _dossier(podcast_feeds=4, sitelinks=2), None)

        from thinktank.models.job import Job as JobModel

        critic_jobs = (
            (await session.execute(select(JobModel).where(JobModel.job_type == "critique_roster"))).scalars().all()
        )
        assert len(critic_jobs) == 1
        assert critic_jobs[0].payload["area"] == area

        # Re-vetting (force) must NOT enqueue a second critique: the
        # one-per-area guard sees the open critic job.
        job = await create_job(
            session, job_type="vet_candidate", payload={"candidate_id": str(candidate.id), "force": True}
        )
        with (
            patch(
                "thinktank.handlers.vet_candidate.gather_evidence",
                new_callable=AsyncMock,
                return_value=_dossier(podcast_feeds=4, sitelinks=2),
            ),
            patch("thinktank.handlers.vet_candidate.assess_domain_fit", new_callable=AsyncMock, return_value=None),
        ):
            await handle_vet_candidate(session, job)
        critic_jobs = (
            (await session.execute(select(JobModel).where(JobModel.job_type == "critique_roster"))).scalars().all()
        )
        assert len(critic_jobs) == 1

    async def test_not_enqueued_while_area_still_vetting(self, session: AsyncSession):
        area = "agentic engineering"
        candidate = await create_candidate_thinker(session, name="First Done", status="pending_llm", search_area=area)
        await create_candidate_thinker(session, name="Still Vetting", status="vetting", search_area=area)
        await _vet_with_fit(session, candidate, _dossier(podcast_feeds=4, sitelinks=2), None)

        from thinktank.models.job import Job as JobModel

        critic_jobs = (
            (await session.execute(select(JobModel).where(JobModel.job_type == "critique_roster"))).scalars().all()
        )
        assert critic_jobs == []
