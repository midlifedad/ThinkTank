"""Contract tests for llm_approval_check handler.

Verifies handler side effects: given a known input payload and mocked LLM,
the handler creates exactly 1 LLMReview row and updates the correct entity.
"""

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import func, select
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


def _mock_llm(result, tokens=500, duration=1200):
    """Create a mock _llm_client context manager."""
    mock_client = AsyncMock()
    mock_client.review = AsyncMock(return_value=(result, tokens, duration))
    mock_client.model = "claude-sonnet-4-20250514"
    return patch(
        "src.thinktank.handlers.llm_approval_check._llm_client",
        mock_client,
    )


@pytest.mark.asyncio
class TestThinkerApprovalContract:
    """Given thinker_approval payload, handler creates 1 LLMReview + updates 1 Thinker."""

    async def test_thinker_approval_contract(self, session: AsyncSession):
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
            decision="approved", reasoning="Meets criteria"
        )

        with _mock_llm(mock_result):
            await handle_llm_approval_check(session, job)

        # Contract: exactly 1 LLMReview row created
        review_count = await session.scalar(
            select(func.count()).select_from(LLMReview)
        )
        assert review_count == 1

        # Contract: Thinker approval_status updated
        await session.refresh(thinker)
        assert thinker.approval_status == "approved"


@pytest.mark.asyncio
class TestSourceApprovalContract:
    """Given source_approval payload, handler creates 1 LLMReview + updates 1 Source."""

    async def test_source_approval_contract(self, session: AsyncSession):
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
            reasoning="Good source",
            approved_backfill_days=60,
        )

        with _mock_llm(mock_result):
            await handle_llm_approval_check(session, job)

        # Contract: exactly 1 LLMReview row created
        review_count = await session.scalar(
            select(func.count()).select_from(LLMReview)
        )
        assert review_count == 1

        # Contract: Source approval_status updated
        await session.refresh(source)
        assert source.approval_status == "approved"
        assert source.approved_backfill_days == 60


@pytest.mark.asyncio
class TestCandidateReviewContract:
    """Given candidate_review payload, handler creates 1 LLMReview + updates CandidateThinker."""

    async def test_candidate_review_contract(self, session: AsyncSession):
        candidate = await create_candidate_thinker(
            session,
            name="Test Candidate",
            normalized_name="test candidate",
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
            reasoning="Well-known expert",
            tier=2,
            categories=["technology"],
        )

        with _mock_llm(mock_result):
            await handle_llm_approval_check(session, job)

        # Contract: exactly 1 LLMReview row created
        review_count = await session.scalar(
            select(func.count()).select_from(LLMReview)
        )
        assert review_count == 1

        # Contract: CandidateThinker status updated (promoted creates a Thinker)
        await session.refresh(candidate)
        assert candidate.status == "promoted"
        assert candidate.thinker_id is not None

        # Contract: New Thinker row created
        thinker_count = await session.scalar(
            select(func.count()).select_from(Thinker).where(
                Thinker.name == "Test Candidate"
            )
        )
        assert thinker_count == 1
