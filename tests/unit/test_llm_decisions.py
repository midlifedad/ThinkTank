"""Unit tests for LLM decision application logic.

Tests use in-memory model instances via factories and mock sessions.
Verifies that decision functions update the correct fields on
thinkers, sources, candidates, and jobs.
"""

import uuid
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.factories import make_candidate_thinker, make_job, make_source, make_thinker
from thinktank.llm.decisions import (
    apply_candidate_decision,
    apply_decision,
    apply_source_decision,
    apply_thinker_decision,
    promote_candidate_to_thinker,
)
from thinktank.llm.schemas import CandidateReviewResponse, SourceApprovalResponse, ThinkerApprovalResponse


@pytest.fixture
def mock_session():
    """Create a mock AsyncSession.

    Default ``session.execute`` returns a result whose ``scalar_one_or_none``
    is None, so slug-collision lookups in decisions.py short-circuit after
    one probe (otherwise AsyncMock returns a truthy MagicMock and the while
    loop never terminates).
    """
    session = AsyncMock()
    session.flush = AsyncMock()
    default_result = MagicMock()
    default_result.scalar_one_or_none.return_value = None
    session.execute.return_value = default_result
    return session


# ---------- apply_thinker_decision ----------


class TestApplyThinkerDecision:
    @pytest.mark.asyncio
    async def test_approved_sets_approval_status(self, mock_session):
        thinker = make_thinker(approval_status="pending_llm")
        mock_session.get.return_value = thinker

        result = ThinkerApprovalResponse(decision="approved", reasoning="Good")
        await apply_thinker_decision(mock_session, thinker.id, result)

        assert thinker.approval_status == "approved"

    @pytest.mark.asyncio
    async def test_rejected_sets_rejected_by_llm(self, mock_session):
        thinker = make_thinker(approval_status="pending_llm")
        mock_session.get.return_value = thinker

        result = ThinkerApprovalResponse(decision="rejected", reasoning="Not relevant")
        await apply_thinker_decision(mock_session, thinker.id, result)

        assert thinker.approval_status == "rejected_by_llm"

    @pytest.mark.asyncio
    async def test_approved_with_modifications_applies_mods(self, mock_session):
        thinker = make_thinker(approval_status="pending_llm")
        mock_session.get.return_value = thinker

        result = ThinkerApprovalResponse(
            decision="approved_with_modifications",
            reasoning="OK with changes",
            modifications={"approved_backfill_days": 90, "approved_source_types": ["podcast_rss"]},
        )
        await apply_thinker_decision(mock_session, thinker.id, result)

        assert thinker.approval_status == "approved"
        assert thinker.approved_backfill_days == 90
        assert thinker.approved_source_types == ["podcast_rss"]

    @pytest.mark.asyncio
    async def test_escalate_sets_pending_human(self, mock_session):
        thinker = make_thinker(approval_status="pending_llm")
        mock_session.get.return_value = thinker

        result = ThinkerApprovalResponse(decision="escalate_to_human", reasoning="Unclear")
        await apply_thinker_decision(mock_session, thinker.id, result)

        assert thinker.approval_status == "pending_human"


# ---------- apply_source_decision ----------


class TestApplySourceDecision:
    @pytest.mark.asyncio
    async def test_approved_sets_status_and_backfill(self, mock_session):
        source = make_source(approval_status="pending_llm")
        mock_session.get.return_value = source

        result = SourceApprovalResponse(decision="approved", reasoning="Good source", approved_backfill_days=30)
        await apply_source_decision(mock_session, source.id, result)

        assert source.approval_status == "approved"
        assert source.approved_backfill_days == 30

    @pytest.mark.asyncio
    async def test_rejected_sets_rejected_by_llm(self, mock_session):
        source = make_source(approval_status="pending_llm")
        mock_session.get.return_value = source

        result = SourceApprovalResponse(decision="rejected", reasoning="Low quality")
        await apply_source_decision(mock_session, source.id, result)

        assert source.approval_status == "rejected_by_llm"

    @pytest.mark.asyncio
    async def test_escalate_sets_pending_human(self, mock_session):
        source = make_source(approval_status="pending_llm")
        mock_session.get.return_value = source

        result = SourceApprovalResponse(decision="escalate_to_human", reasoning="Unsure")
        await apply_source_decision(mock_session, source.id, result)

        assert source.approval_status == "pending_human"


# ---------- apply_candidate_decision ----------


class TestApplyCandidateDecision:
    @pytest.mark.asyncio
    async def test_approved_calls_promote(self, mock_session):
        candidate = make_candidate_thinker(status="pending_llm")
        mock_session.get.return_value = candidate
        review_id = uuid.uuid4()

        result = CandidateReviewResponse(
            decision="approved",
            reasoning="Well-known expert",
            tier=2,
            categories=["AI"],
            initial_sources=["https://example.com/rss"],
        )

        with patch("thinktank.llm.decisions.promote_candidate_to_thinker", new_callable=AsyncMock) as mock_promote:
            mock_promote.return_value = make_thinker()
            await apply_candidate_decision(mock_session, candidate.id, result, review_id)
            mock_promote.assert_called_once()

        assert candidate.llm_review_id == review_id
        assert candidate.reviewed_by == "llm"
        assert isinstance(candidate.reviewed_at, datetime)

    @pytest.mark.asyncio
    async def test_rejected_sets_status(self, mock_session):
        candidate = make_candidate_thinker(status="pending_llm")
        mock_session.get.return_value = candidate
        review_id = uuid.uuid4()

        result = CandidateReviewResponse(decision="rejected", reasoning="Not notable")
        await apply_candidate_decision(mock_session, candidate.id, result, review_id)

        assert candidate.status == "rejected"
        assert candidate.llm_review_id == review_id

    @pytest.mark.asyncio
    async def test_duplicate_sets_rejected_duplicate(self, mock_session):
        candidate = make_candidate_thinker(status="pending_llm")
        mock_session.get.return_value = candidate
        review_id = uuid.uuid4()

        result = CandidateReviewResponse(decision="duplicate", reasoning="Already exists", duplicate_of="existing-slug")
        await apply_candidate_decision(mock_session, candidate.id, result, review_id)

        assert candidate.status == "rejected_duplicate"
        assert candidate.reviewed_by == "llm"

    @pytest.mark.asyncio
    async def test_need_more_appearances_sets_needs_more_data(self, mock_session):
        candidate = make_candidate_thinker(status="pending_llm")
        mock_session.get.return_value = candidate
        review_id = uuid.uuid4()

        result = CandidateReviewResponse(decision="need_more_appearances", reasoning="Only seen once")
        await apply_candidate_decision(mock_session, candidate.id, result, review_id)

        assert candidate.status == "needs_more_data"

    @pytest.mark.asyncio
    async def test_escalate_sets_pending_human(self, mock_session):
        candidate = make_candidate_thinker(status="pending_llm")
        mock_session.get.return_value = candidate
        review_id = uuid.uuid4()

        result = CandidateReviewResponse(decision="escalate_to_human", reasoning="Unsure")
        await apply_candidate_decision(mock_session, candidate.id, result, review_id)

        assert candidate.status == "pending_human"


# ---------- promote_candidate_to_thinker ----------


class TestPromoteCandidateToThinker:
    @pytest.mark.asyncio
    async def test_creates_thinker_from_candidate(self, mock_session):
        candidate = make_candidate_thinker(name="Jane Smith", normalized_name="jane smith", status="pending_llm")

        result = CandidateReviewResponse(
            decision="approved", reasoning="Excellent expert", tier=2, categories=["Philosophy", "Ethics"]
        )

        thinker = await promote_candidate_to_thinker(mock_session, candidate, result)

        assert thinker.name == "Jane Smith"
        assert thinker.slug == "jane-smith"
        assert thinker.tier == 2
        assert thinker.approval_status == "approved"
        mock_session.add.assert_called()

    @pytest.mark.asyncio
    async def test_links_candidate_to_thinker(self, mock_session):
        candidate = make_candidate_thinker(name="Test Person", status="pending_llm")

        result = CandidateReviewResponse(decision="approved", reasoning="Good", tier=3)

        thinker = await promote_candidate_to_thinker(mock_session, candidate, result)

        assert candidate.thinker_id == thinker.id
        assert candidate.status == "promoted"

    @pytest.mark.asyncio
    async def test_default_tier_3(self, mock_session):
        candidate = make_candidate_thinker(name="Test", status="pending_llm")

        result = CandidateReviewResponse(
            decision="approved",
            reasoning="OK",
            # No tier specified
        )

        thinker = await promote_candidate_to_thinker(mock_session, candidate, result)

        assert thinker.tier == 3  # Default tier

    @pytest.mark.asyncio
    async def test_enqueues_discover_and_rescan_jobs(self, mock_session):
        """HANDLERS-REVIEW ME-03: promotion must enqueue BOTH a discover_thinker
        job (forward-looking source discovery) AND a rescan_cataloged_for_thinker
        job (retroactive episode match). If either is dropped, the cascade
        breaks silently.
        """
        candidate = make_candidate_thinker(name="New Expert", status="pending_llm")
        result = CandidateReviewResponse(decision="approved", reasoning="Strong signal", tier=2)

        thinker = await promote_candidate_to_thinker(mock_session, candidate, result)

        added = [call.args[0] for call in mock_session.add.call_args_list]
        job_types = {obj.job_type for obj in added if hasattr(obj, "job_type")}
        assert "discover_thinker" in job_types, "promotion must enqueue discover_thinker"
        assert "rescan_cataloged_for_thinker" in job_types, "promotion must enqueue rescan_cataloged_for_thinker"

        jobs_by_type = {obj.job_type: obj for obj in added if hasattr(obj, "job_type")}
        assert jobs_by_type["discover_thinker"].payload == {"thinker_id": str(thinker.id)}
        assert jobs_by_type["rescan_cataloged_for_thinker"].payload == {
            "thinker_id": str(thinker.id),
            "thinker_name": thinker.name,
        }

    @pytest.mark.asyncio
    async def test_slug_collision_appends_suffix(self, mock_session):
        """ADMIN LO-02 (decisions path): when _slugify collides with an
        existing thinker, _unique_thinker_slug must append -2, -3, ... until
        a free slug is found. Otherwise promotion raises IntegrityError.
        """
        candidate = make_candidate_thinker(name="Jane Smith", status="pending_llm")
        result = CandidateReviewResponse(decision="approved", reasoning="ok", tier=3)

        # First two probes collide; third is free.
        collide = MagicMock()
        collide.scalar_one_or_none.return_value = uuid.uuid4()
        free = MagicMock()
        free.scalar_one_or_none.return_value = None
        mock_session.execute.side_effect = [collide, collide, free]

        thinker = await promote_candidate_to_thinker(mock_session, candidate, result)

        assert thinker.slug == "jane-smith-3"


# ---------- apply_decision (dispatcher) ----------


class TestApplyDecision:
    @pytest.mark.asyncio
    async def test_dispatches_thinker_approval(self, mock_session):
        thinker = make_thinker(approval_status="pending_llm")
        mock_session.get.return_value = thinker
        review_id = uuid.uuid4()
        target_id = thinker.id

        result = ThinkerApprovalResponse(decision="approved", reasoning="Good")
        await apply_decision(mock_session, "thinker_approval", target_id, None, result, review_id)

        assert thinker.approval_status == "approved"

    @pytest.mark.asyncio
    async def test_dispatches_source_approval(self, mock_session):
        source = make_source(approval_status="pending_llm")
        mock_session.get.return_value = source
        review_id = uuid.uuid4()

        result = SourceApprovalResponse(decision="approved", reasoning="OK", approved_backfill_days=14)
        await apply_decision(mock_session, "source_approval", source.id, None, result, review_id)

        assert source.approval_status == "approved"

    @pytest.mark.asyncio
    async def test_dispatches_candidate_review(self, mock_session):
        candidate = make_candidate_thinker(status="pending_llm")
        mock_session.get.return_value = candidate
        review_id = uuid.uuid4()

        result = CandidateReviewResponse(decision="rejected", reasoning="Nope")
        await apply_decision(mock_session, "candidate_review", candidate.id, None, result, review_id)

        assert candidate.status == "rejected"

    @pytest.mark.asyncio
    async def test_updates_pending_job_on_approval(self, mock_session):
        thinker = make_thinker(approval_status="pending_llm")
        job = make_job(status="awaiting_llm")

        # Return thinker first, then job
        mock_session.get.side_effect = [thinker, job]

        review_id = uuid.uuid4()
        result = ThinkerApprovalResponse(decision="approved", reasoning="Good")
        await apply_decision(mock_session, "thinker_approval", thinker.id, job.id, result, review_id)

        assert job.llm_review_id == review_id
        assert job.status == "pending"

    @pytest.mark.asyncio
    async def test_updates_pending_job_on_rejection(self, mock_session):
        thinker = make_thinker(approval_status="pending_llm")
        job = make_job(status="awaiting_llm")

        mock_session.get.side_effect = [thinker, job]

        review_id = uuid.uuid4()
        result = ThinkerApprovalResponse(decision="rejected", reasoning="No")
        await apply_decision(mock_session, "thinker_approval", thinker.id, job.id, result, review_id)

        assert job.llm_review_id == review_id
        assert job.status == "done"  # No further action for rejected

    @pytest.mark.asyncio
    async def test_candidate_review_updates_llm_review_id(self, mock_session):
        candidate = make_candidate_thinker(status="pending_llm")
        mock_session.get.return_value = candidate
        review_id = uuid.uuid4()

        result = CandidateReviewResponse(decision="rejected", reasoning="No")
        await apply_decision(mock_session, "candidate_review", candidate.id, None, result, review_id)

        assert candidate.llm_review_id == review_id
