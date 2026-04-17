"""Unit tests for LLM decision application logic.

Tests use in-memory model instances via factories and mock sessions.
Verifies that decision functions update the correct fields on
thinkers, sources, candidates, and jobs.
"""

import uuid
from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest

from tests.factories import make_candidate_thinker, make_job, make_source, make_thinker
from thinktank.llm.decisions import (
    apply_candidate_decision,
    apply_decision,
    apply_source_decision,
    apply_thinker_decision,
    promote_candidate_to_thinker,
)
from thinktank.llm.schemas import (
    CandidateReviewResponse,
    SourceApprovalResponse,
    ThinkerApprovalResponse,
)


@pytest.fixture
def mock_session():
    """Create a mock AsyncSession."""
    session = AsyncMock()
    session.flush = AsyncMock()
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
        source = make_source(thinker_id=uuid.uuid4(), approval_status="pending_llm")
        mock_session.get.return_value = source

        result = SourceApprovalResponse(
            decision="approved",
            reasoning="Good source",
            approved_backfill_days=30,
        )
        await apply_source_decision(mock_session, source.id, result)

        assert source.approval_status == "approved"
        assert source.approved_backfill_days == 30

    @pytest.mark.asyncio
    async def test_rejected_sets_rejected_by_llm(self, mock_session):
        source = make_source(thinker_id=uuid.uuid4(), approval_status="pending_llm")
        mock_session.get.return_value = source

        result = SourceApprovalResponse(decision="rejected", reasoning="Low quality")
        await apply_source_decision(mock_session, source.id, result)

        assert source.approval_status == "rejected_by_llm"

    @pytest.mark.asyncio
    async def test_escalate_sets_pending_human(self, mock_session):
        source = make_source(thinker_id=uuid.uuid4(), approval_status="pending_llm")
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

        result = CandidateReviewResponse(
            decision="duplicate",
            reasoning="Already exists",
            duplicate_of="existing-slug",
        )
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
        candidate = make_candidate_thinker(
            name="Jane Smith",
            normalized_name="jane smith",
            status="pending_llm",
        )

        result = CandidateReviewResponse(
            decision="approved",
            reasoning="Excellent expert",
            tier=2,
            categories=["Philosophy", "Ethics"],
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

        result = CandidateReviewResponse(
            decision="approved",
            reasoning="Good",
            tier=3,
        )

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
        source = make_source(thinker_id=uuid.uuid4(), approval_status="pending_llm")
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
