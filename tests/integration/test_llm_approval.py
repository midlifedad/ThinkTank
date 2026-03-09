"""Integration tests for LLM approval flows.

Tests end-to-end thinker/source/candidate approval with mocked LLM client.
Verifies database state changes, audit trail creation, and decision application.

All tests mock the LLM client at the handler module level:
  src.thinktank.handlers.llm_approval_check._llm_client.review
"""

from unittest.mock import AsyncMock, patch

import anthropic
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from src.thinktank.handlers.llm_approval_check import handle_llm_approval_check
from src.thinktank.llm.schemas import (
    CandidateReviewResponse,
    SourceApprovalResponse,
    ThinkerApprovalResponse,
)
from src.thinktank.models.review import LLMReview
from src.thinktank.models.thinker import Thinker

from tests.factories import (
    create_candidate_thinker,
    create_job,
    create_source,
    create_thinker,
)


def _mock_llm_review(result, tokens=500, duration=1200):
    """Create a mock for _llm_client.review returning the given result."""
    return AsyncMock(return_value=(result, tokens, duration))


@pytest.mark.asyncio
class TestThinkerApprovalFlows:
    """Integration tests for thinker approval via LLM."""

    async def test_thinker_approval_approved(self, session: AsyncSession):
        """Thinker with pending_llm status gets approved by LLM."""
        thinker = await create_thinker(
            session, approval_status="pending_llm"
        )
        job = await create_job(
            session,
            job_type="llm_approval_check",
            payload={
                "review_type": "thinker_approval",
                "target_id": str(thinker.id),
            },
        )

        mock_result = ThinkerApprovalResponse(
            decision="approved", reasoning="Valid thinker with strong credentials"
        )

        with patch(
            "src.thinktank.handlers.llm_approval_check._llm_client"
        ) as mock_client:
            mock_client.review = _mock_llm_review(mock_result)
            mock_client.model = "claude-sonnet-4-20250514"

            await handle_llm_approval_check(session, job)

        # Verify thinker status updated
        await session.refresh(thinker)
        assert thinker.approval_status == "approved"

        # Verify LLMReview row exists
        result = await session.execute(
            select(LLMReview).where(LLMReview.review_type == "thinker_approval")
        )
        review = result.scalar_one()
        assert review.decision == "approved"
        assert review.decision_reasoning == "Valid thinker with strong credentials"

    async def test_thinker_approval_rejected(self, session: AsyncSession):
        """Thinker gets rejected by LLM, status becomes rejected_by_llm."""
        thinker = await create_thinker(
            session, approval_status="pending_llm"
        )
        job = await create_job(
            session,
            job_type="llm_approval_check",
            payload={
                "review_type": "thinker_approval",
                "target_id": str(thinker.id),
            },
        )

        mock_result = ThinkerApprovalResponse(
            decision="rejected", reasoning="Not a recognized expert"
        )

        with patch(
            "src.thinktank.handlers.llm_approval_check._llm_client"
        ) as mock_client:
            mock_client.review = _mock_llm_review(mock_result)
            mock_client.model = "claude-sonnet-4-20250514"

            await handle_llm_approval_check(session, job)

        await session.refresh(thinker)
        assert thinker.approval_status == "rejected_by_llm"

    async def test_thinker_approval_escalated(self, session: AsyncSession):
        """Thinker escalated to human, status becomes pending_human."""
        thinker = await create_thinker(
            session, approval_status="pending_llm"
        )
        job = await create_job(
            session,
            job_type="llm_approval_check",
            payload={
                "review_type": "thinker_approval",
                "target_id": str(thinker.id),
            },
        )

        mock_result = ThinkerApprovalResponse(
            decision="escalate_to_human",
            reasoning="Borderline case, needs human judgment",
        )

        with patch(
            "src.thinktank.handlers.llm_approval_check._llm_client"
        ) as mock_client:
            mock_client.review = _mock_llm_review(mock_result)
            mock_client.model = "claude-sonnet-4-20250514"

            await handle_llm_approval_check(session, job)

        await session.refresh(thinker)
        assert thinker.approval_status == "pending_human"


@pytest.mark.asyncio
class TestSourceApprovalFlows:
    """Integration tests for source approval via LLM."""

    async def test_source_approval_approved(self, session: AsyncSession):
        """Source gets approved with backfill_days set."""
        thinker = await create_thinker(session, approval_status="approved")
        source = await create_source(
            session,
            thinker_id=thinker.id,
            approval_status="pending_llm",
        )
        job = await create_job(
            session,
            job_type="llm_approval_check",
            payload={
                "review_type": "source_approval",
                "target_id": str(source.id),
            },
        )

        mock_result = SourceApprovalResponse(
            decision="approved",
            reasoning="Quality podcast source",
            approved_backfill_days=90,
        )

        with patch(
            "src.thinktank.handlers.llm_approval_check._llm_client"
        ) as mock_client:
            mock_client.review = _mock_llm_review(mock_result)
            mock_client.model = "claude-sonnet-4-20250514"

            await handle_llm_approval_check(session, job)

        await session.refresh(source)
        assert source.approval_status == "approved"
        assert source.approved_backfill_days == 90


@pytest.mark.asyncio
class TestCandidateReviewFlows:
    """Integration tests for candidate batch review via LLM."""

    async def test_candidate_promotion_creates_thinker(self, session: AsyncSession):
        """Approved candidate creates a new Thinker row and links to it."""
        candidate = await create_candidate_thinker(
            session,
            name="Dr. Jane Expert",
            normalized_name="dr jane expert",
            status="pending_llm",
            appearance_count=5,
        )
        job = await create_job(
            session,
            job_type="llm_approval_check",
            payload={
                "review_type": "candidate_review",
                "target_id": str(candidate.id),
                "candidate_ids": [str(candidate.id)],
            },
        )

        mock_result = CandidateReviewResponse(
            decision="approved",
            reasoning="Well-known expert in economics",
            tier=2,
            categories=["economics"],
            initial_sources=["podcast_rss"],
        )

        with patch(
            "src.thinktank.handlers.llm_approval_check._llm_client"
        ) as mock_client:
            mock_client.review = _mock_llm_review(mock_result)
            mock_client.model = "claude-sonnet-4-20250514"

            await handle_llm_approval_check(session, job)

        # Verify candidate status updated
        await session.refresh(candidate)
        assert candidate.status == "promoted"
        assert candidate.thinker_id is not None

        # Verify new Thinker was created
        new_thinker = await session.get(Thinker, candidate.thinker_id)
        assert new_thinker is not None
        assert new_thinker.name == "Dr. Jane Expert"
        assert new_thinker.approval_status == "approved"
        assert new_thinker.tier == 2


@pytest.mark.asyncio
class TestAuditTrail:
    """Integration tests for audit trail completeness."""

    async def test_audit_trail_completeness(self, session: AsyncSession):
        """All LLMReview fields are populated after handler runs."""
        thinker = await create_thinker(
            session, approval_status="pending_llm"
        )
        job = await create_job(
            session,
            job_type="llm_approval_check",
            payload={
                "review_type": "thinker_approval",
                "target_id": str(thinker.id),
            },
        )

        mock_result = ThinkerApprovalResponse(
            decision="approved",
            reasoning="Valid thinker",
            flagged_items=["minor concern about coverage"],
        )

        with patch(
            "src.thinktank.handlers.llm_approval_check._llm_client"
        ) as mock_client:
            mock_client.review = _mock_llm_review(mock_result, tokens=750, duration=2100)
            mock_client.model = "claude-sonnet-4-20250514"

            await handle_llm_approval_check(session, job)

        result = await session.execute(select(LLMReview))
        review = result.scalar_one()

        # All audit fields must be non-null
        assert review.review_type == "thinker_approval"
        assert review.trigger == "job_gate"
        assert review.context_snapshot is not None
        assert isinstance(review.context_snapshot, dict)
        assert review.prompt_used is not None
        assert len(review.prompt_used) > 0
        assert review.llm_response is not None
        assert review.decision == "approved"
        assert review.decision_reasoning == "Valid thinker"
        assert review.model == "claude-sonnet-4-20250514"
        assert review.tokens_used == 750
        assert review.duration_ms == 2100
        assert review.flagged_items == ["minor concern about coverage"]

    async def test_pending_job_linked(self, session: AsyncSession):
        """Pending job gets llm_review_id set after approval."""
        thinker = await create_thinker(
            session, approval_status="pending_llm"
        )
        # Create a pending job that the thinker is waiting on
        pending_job = await create_job(
            session,
            job_type="fetch_podcast_feed",
            payload={"thinker_id": str(thinker.id)},
            status="pending",
        )

        approval_job = await create_job(
            session,
            job_type="llm_approval_check",
            payload={
                "review_type": "thinker_approval",
                "target_id": str(thinker.id),
                "pending_job_id": str(pending_job.id),
            },
        )

        mock_result = ThinkerApprovalResponse(
            decision="approved", reasoning="Valid thinker"
        )

        with patch(
            "src.thinktank.handlers.llm_approval_check._llm_client"
        ) as mock_client:
            mock_client.review = _mock_llm_review(mock_result)
            mock_client.model = "claude-sonnet-4-20250514"

            await handle_llm_approval_check(session, approval_job)

        await session.refresh(pending_job)
        assert pending_job.llm_review_id is not None
        assert pending_job.status == "pending"  # Ready for processing


@pytest.mark.asyncio
class TestAPIUnavailability:
    """Integration tests for API unavailability handling."""

    async def test_api_unavailable_raises(self, session: AsyncSession):
        """API connection error propagates for worker loop retry."""
        thinker = await create_thinker(
            session, approval_status="pending_llm"
        )
        job = await create_job(
            session,
            job_type="llm_approval_check",
            payload={
                "review_type": "thinker_approval",
                "target_id": str(thinker.id),
            },
        )

        import httpx

        api_error = anthropic.APIConnectionError(
            request=httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        )

        with patch(
            "src.thinktank.handlers.llm_approval_check._llm_client"
        ) as mock_client:
            mock_client.review = AsyncMock(side_effect=api_error)

            with pytest.raises(anthropic.APIConnectionError):
                await handle_llm_approval_check(session, job)
