"""Contract tests for the roster critic (critique_roster handler).

Contract: given a vetted area slate and a (mocked) LLM verdict, the
handler persists one RosterCritique row, nominates MISSING names as
normal candidates with vet jobs (deduped against existing thinkers and
candidates), and never mutates existing verdicts.
"""

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.factories import create_candidate_thinker, create_job, create_thinker
from thinktank.handlers.critique_roster import MisrankedEntry, MissingEntry, RosterVerdict, handle_critique_roster
from thinktank.llm.client import LLMUsage
from thinktank.models.candidate import CandidateThinker, RosterCritique
from thinktank.models.job import Job

pytestmark = pytest.mark.anyio

AREA = "AI coding and agentic engineering"


def _mock_llm(verdict: RosterVerdict):
    return patch(
        "thinktank.handlers.critique_roster._client.review",
        new=AsyncMock(return_value=(verdict, LLMUsage(input_tokens=500, output_tokens=200), 10)),
    )


async def _run(session: AsyncSession, area=AREA) -> None:
    job = await create_job(session, job_type="critique_roster", payload={"area": area})
    await handle_critique_roster(session, job)


class TestCritiqueRoster:
    async def test_persists_critique_and_nominates_missing(self, session: AsyncSession):
        await create_candidate_thinker(
            session,
            name="Eminent Elsewhere",
            normalized_name="eminent elsewhere",
            status="promoted",
            search_area=AREA,
            qualification_score=75,
        )
        verdict = RosterVerdict(
            analysis="Walked the slate.",
            misranked=[MisrankedEntry(name="Eminent Elsewhere", issue="Peripheral to this domain")],
            missing=[MissingEntry(name="Andrej Karpathy", why="Defined modern AI-assisted coding practice")],
        )
        with _mock_llm(verdict):
            await _run(session)

        critique = (await session.execute(select(RosterCritique))).scalars().one()
        assert critique.search_area == AREA
        assert critique.candidates_reviewed == 1
        assert critique.nominated == 1
        assert critique.critique["misranked"][0]["name"] == "Eminent Elsewhere"

        nominee = (
            (await session.execute(select(CandidateThinker).where(CandidateThinker.name == "Andrej Karpathy")))
            .scalars()
            .one()
        )
        assert nominee.status == "vetting"
        assert nominee.seed_source == "roster_critic"
        assert nominee.search_area == AREA
        vet_jobs = (await session.execute(select(Job).where(Job.job_type == "vet_candidate"))).scalars().all()
        assert [j.payload["candidate_id"] for j in vet_jobs] == [str(nominee.id)]

    async def test_dedupes_against_existing_thinkers_and_candidates(self, session: AsyncSession):
        await create_candidate_thinker(
            session,
            name="Someone Promoted",
            normalized_name="someone promoted",
            status="promoted",
            search_area=AREA,
            qualification_score=60,
        )
        await create_thinker(session, name="Existing Thinker", slug="existing-thinker")
        await create_candidate_thinker(
            session, name="Open Candidate", normalized_name="open candidate", status="pending_human"
        )
        verdict = RosterVerdict(
            analysis="Walked the slate.",
            missing=[
                MissingEntry(name="Existing Thinker", why="already tracked"),
                MissingEntry(name="Open Candidate", why="already a candidate"),
            ],
        )
        with _mock_llm(verdict):
            await _run(session)

        critique = (await session.execute(select(RosterCritique))).scalars().one()
        assert critique.nominated == 0
        vet_jobs = (await session.execute(select(Job).where(Job.job_type == "vet_candidate"))).scalars().all()
        assert vet_jobs == []

    async def test_clean_slate_persists_empty_critique(self, session: AsyncSession):
        await create_candidate_thinker(
            session,
            name="Rightly Promoted",
            normalized_name="rightly promoted",
            status="promoted",
            search_area=AREA,
            qualification_score=70,
        )
        with _mock_llm(RosterVerdict(analysis="Slate genuinely holds up.")):
            await _run(session)
        critique = (await session.execute(select(RosterCritique))).scalars().one()
        assert critique.critique["misranked"] == []
        assert critique.critique["missing"] == []
        assert critique.critique["analysis"]  # the reasoning persists for audit
        assert critique.nominated == 0

    async def test_unknown_area_skips_without_llm(self, session: AsyncSession):
        review = AsyncMock()
        with patch("thinktank.handlers.critique_roster._client.review", new=review):
            await _run(session, area="never-searched-area")
        review.assert_not_awaited()
        assert (await session.execute(select(RosterCritique))).scalars().all() == []


class TestJudgePathLiveness:
    async def test_last_judge_decision_triggers_critic(self, session: AsyncSession):
        """When the area settles on a JUDGE decision (not a vet completion),
        the critic must still auto-fire -- the first live run only got its
        critique by lucky job ordering."""
        import uuid as _uuid

        from tests.factories import create_llm_review
        from thinktank.llm.decisions import apply_candidate_decision
        from thinktank.llm.schemas import CandidateReviewResponse

        candidate = await create_candidate_thinker(
            session,
            name="Last One Judged",
            normalized_name="last one judged",
            status="awaiting_llm",
            search_area=AREA,
            qualification_score=40,
        )
        review = await create_llm_review(session)
        await apply_candidate_decision(
            session,
            candidate.id,
            CandidateReviewResponse(decision="rejected", reasoning="peripheral to the area"),
            review.id,
        )

        critic_jobs = (await session.execute(select(Job).where(Job.job_type == "critique_roster"))).scalars().all()
        assert len(critic_jobs) == 1
        assert critic_jobs[0].payload["area"] == AREA
        assert _uuid.UUID(str(critic_jobs[0].id))  # sanity: real row
