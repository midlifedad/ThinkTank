"""Unit tests for llm_approval_check handler.

Tests handler dispatch, LLMReview audit trail creation, decision application,
and error handling for unknown/missing payload fields.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from thinktank.llm.schemas import (
    CandidateReviewResponse,
    SourceApprovalResponse,
    ThinkerApprovalResponse,
)
from thinktank.models.job import Job
from thinktank.models.review import LLMReview


def _make_job(review_type: str, target_id: str | None = None, **extra_payload) -> Job:
    """Helper to create a mock job with llm_approval_check payload."""
    payload = {"review_type": review_type}
    if target_id is not None:
        payload["target_id"] = target_id
    payload.update(extra_payload)
    job = MagicMock(spec=Job)
    job.payload = payload
    job.id = uuid.uuid4()
    return job


class TestHandlerDispatchesThinkerApproval:
    """Handler correctly dispatches thinker_approval review type."""

    @pytest.mark.asyncio
    async def test_dispatches_thinker_approval(self):
        from thinktank.handlers.llm_approval_check import handle_llm_approval_check

        target_id = uuid.uuid4()
        job = _make_job("thinker_approval", str(target_id))

        mock_context = {"proposed_thinker": {"name": "Test"}}
        mock_result = ThinkerApprovalResponse(decision="approved", reasoning="Valid thinker")

        session = AsyncMock()
        # Make session.flush() and session.commit() awaitable
        session.flush = AsyncMock()
        session.commit = AsyncMock()

        with (
            patch(
                "thinktank.handlers.llm_approval_check.build_thinker_approval_context",
                new_callable=AsyncMock,
                return_value=mock_context,
            ) as mock_snapshot,
            patch(
                "thinktank.handlers.llm_approval_check.build_thinker_approval_prompt",
                return_value=("system", "user"),
            ),
            patch("thinktank.handlers.llm_approval_check._llm_client") as mock_client,
            patch(
                "thinktank.handlers.llm_approval_check.apply_decision",
                new_callable=AsyncMock,
            ) as mock_apply,
        ):
            mock_client.review = AsyncMock(return_value=(mock_result, 500, 1200))
            mock_client.model = "claude-sonnet-4-20250514"

            await handle_llm_approval_check(session, job)

            mock_snapshot.assert_called_once_with(session, target_id)
            mock_apply.assert_called_once()
            args = mock_apply.call_args[0]
            assert args[1] == "thinker_approval"


class TestHandlerDispatchesSourceApproval:
    """Handler correctly dispatches source_approval review type."""

    @pytest.mark.asyncio
    async def test_dispatches_source_approval(self):
        from thinktank.handlers.llm_approval_check import handle_llm_approval_check

        target_id = uuid.uuid4()
        job = _make_job("source_approval", str(target_id))

        mock_context = {"source": {"name": "Test Source"}}
        mock_result = SourceApprovalResponse(decision="approved", reasoning="Valid source", approved_backfill_days=90)

        session = AsyncMock()
        session.flush = AsyncMock()
        session.commit = AsyncMock()

        with (
            patch(
                "thinktank.handlers.llm_approval_check.build_source_approval_context",
                new_callable=AsyncMock,
                return_value=mock_context,
            ) as mock_snapshot,
            patch(
                "thinktank.handlers.llm_approval_check.build_source_approval_prompt",
                return_value=("system", "user"),
            ),
            patch("thinktank.handlers.llm_approval_check._llm_client") as mock_client,
            patch(
                "thinktank.handlers.llm_approval_check.apply_decision",
                new_callable=AsyncMock,
            ),
        ):
            mock_client.review = AsyncMock(return_value=(mock_result, 400, 1100))
            mock_client.model = "claude-sonnet-4-20250514"

            await handle_llm_approval_check(session, job)

            mock_snapshot.assert_called_once_with(session, target_id)


class TestHandlerDispatchesCandidateReview:
    """Handler correctly dispatches candidate_review review type."""

    @pytest.mark.asyncio
    async def test_dispatches_candidate_review(self):
        from thinktank.handlers.llm_approval_check import handle_llm_approval_check

        target_id = uuid.uuid4()
        candidate_ids = [str(uuid.uuid4()), str(uuid.uuid4())]
        job = _make_job("candidate_review", str(target_id), candidate_ids=candidate_ids)

        mock_context = {"candidates": []}
        mock_result = CandidateReviewResponse(decision="approved", reasoning="Valid candidate", tier=2)

        session = AsyncMock()
        session.flush = AsyncMock()
        session.commit = AsyncMock()

        with (
            patch(
                "thinktank.handlers.llm_approval_check.build_candidate_review_context",
                new_callable=AsyncMock,
                return_value=mock_context,
            ) as mock_snapshot,
            patch(
                "thinktank.handlers.llm_approval_check.build_candidate_review_prompt",
                return_value=("system", "user"),
            ),
            patch("thinktank.handlers.llm_approval_check._llm_client") as mock_client,
            patch(
                "thinktank.handlers.llm_approval_check.apply_decision",
                new_callable=AsyncMock,
            ),
        ):
            mock_client.review = AsyncMock(return_value=(mock_result, 300, 900))
            mock_client.model = "claude-sonnet-4-20250514"

            await handle_llm_approval_check(session, job)

            # candidate_review uses candidate_ids from payload
            mock_snapshot.assert_called_once()


class TestHandlerCreatesAuditTrail:
    """Handler creates LLMReview row with all required audit fields."""

    @pytest.mark.asyncio
    async def test_creates_llm_review_with_all_fields(self):
        from thinktank.handlers.llm_approval_check import handle_llm_approval_check

        target_id = uuid.uuid4()
        job = _make_job("thinker_approval", str(target_id))

        mock_context = {"proposed_thinker": {"name": "Test"}}
        mock_result = ThinkerApprovalResponse(
            decision="approved",
            reasoning="Valid thinker",
            flagged_items=["minor concern"],
        )

        session = AsyncMock()
        session.flush = AsyncMock()
        session.commit = AsyncMock()

        added_objects = []
        session.add = lambda obj: added_objects.append(obj)

        with (
            patch(
                "thinktank.handlers.llm_approval_check.build_thinker_approval_context",
                new_callable=AsyncMock,
                return_value=mock_context,
            ),
            patch(
                "thinktank.handlers.llm_approval_check.build_thinker_approval_prompt",
                return_value=("system_prompt_text", "user_prompt_text"),
            ),
            patch("thinktank.handlers.llm_approval_check._llm_client") as mock_client,
            patch(
                "thinktank.handlers.llm_approval_check.apply_decision",
                new_callable=AsyncMock,
            ),
        ):
            mock_client.review = AsyncMock(return_value=(mock_result, 500, 1200))
            mock_client.model = "claude-sonnet-4-20250514"

            await handle_llm_approval_check(session, job)

            # Find the LLMReview object that was added to session
            reviews = [obj for obj in added_objects if isinstance(obj, LLMReview)]
            assert len(reviews) == 1
            review = reviews[0]

            assert review.review_type == "thinker_approval"
            assert review.trigger == "job_gate"
            assert review.context_snapshot == mock_context
            assert "system_prompt_text" in review.prompt_used
            assert "user_prompt_text" in review.prompt_used
            assert review.decision == "approved"
            assert review.decision_reasoning == "Valid thinker"
            assert review.model == "claude-sonnet-4-20250514"
            assert review.tokens_used == 500
            assert review.duration_ms == 1200
            assert review.llm_response is not None
            assert review.flagged_items == ["minor concern"]


class TestHandlerCallsApplyDecision:
    """Handler calls apply_decision with correct arguments."""

    @pytest.mark.asyncio
    async def test_calls_apply_decision_correctly(self):
        from thinktank.handlers.llm_approval_check import handle_llm_approval_check

        target_id = uuid.uuid4()
        pending_job_id = uuid.uuid4()
        job = _make_job("thinker_approval", str(target_id), pending_job_id=str(pending_job_id))

        mock_context = {"proposed_thinker": {"name": "Test"}}
        mock_result = ThinkerApprovalResponse(decision="approved", reasoning="Valid thinker")

        session = AsyncMock()
        session.flush = AsyncMock()
        session.commit = AsyncMock()
        session.add = MagicMock()

        with (
            patch(
                "thinktank.handlers.llm_approval_check.build_thinker_approval_context",
                new_callable=AsyncMock,
                return_value=mock_context,
            ),
            patch(
                "thinktank.handlers.llm_approval_check.build_thinker_approval_prompt",
                return_value=("system", "user"),
            ),
            patch("thinktank.handlers.llm_approval_check._llm_client") as mock_client,
            patch(
                "thinktank.handlers.llm_approval_check.apply_decision",
                new_callable=AsyncMock,
            ) as mock_apply,
        ):
            mock_client.review = AsyncMock(return_value=(mock_result, 500, 1200))
            mock_client.model = "claude-sonnet-4-20250514"

            await handle_llm_approval_check(session, job)

            mock_apply.assert_called_once()
            args = mock_apply.call_args[0]
            assert args[0] == session
            assert args[1] == "thinker_approval"
            assert args[2] == target_id
            assert args[3] == pending_job_id
            assert args[4] == mock_result
            # args[5] is the review.id (UUID)
            assert isinstance(args[5], uuid.UUID)


class TestHandlerErrorCases:
    """Handler raises on unknown review_type or missing required fields."""

    @pytest.mark.asyncio
    async def test_raises_on_unknown_review_type(self):
        from thinktank.handlers.llm_approval_check import handle_llm_approval_check

        job = _make_job("unknown_type", str(uuid.uuid4()))
        session = AsyncMock()

        with pytest.raises(ValueError, match="Unknown review_type"):
            await handle_llm_approval_check(session, job)

    @pytest.mark.asyncio
    async def test_raises_on_missing_review_type(self):
        from thinktank.handlers.llm_approval_check import handle_llm_approval_check

        job = MagicMock(spec=Job)
        job.payload = {"target_id": str(uuid.uuid4())}
        session = AsyncMock()

        with pytest.raises(ValueError, match="review_type"):
            await handle_llm_approval_check(session, job)

    @pytest.mark.asyncio
    async def test_raises_on_missing_target_id_for_non_candidate(self):
        from thinktank.handlers.llm_approval_check import handle_llm_approval_check

        job = MagicMock(spec=Job)
        job.payload = {"review_type": "thinker_approval"}
        session = AsyncMock()

        with pytest.raises(ValueError, match="target_id"):
            await handle_llm_approval_check(session, job)
