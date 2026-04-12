"""LLM approval check handler for the job queue.

Orchestrates LLM-gated approval for thinkers, sources, and candidates.
When any entity enters `awaiting_llm` status, an `llm_approval_check` job
is created. This handler builds context, calls the LLM, logs the audit trail,
and applies the decision.

Spec reference: Section 8.1 (LLM Supervisor approval pipeline).
"""

import sys
import uuid

import structlog
from sqlalchemy.ext.asyncio import AsyncSession
from src.thinktank.llm.client import LLMClient
from src.thinktank.llm.decisions import apply_decision
from src.thinktank.llm.prompts import (
    build_candidate_review_prompt,  # noqa: F401 -- resolved dynamically
    build_source_approval_prompt,  # noqa: F401 -- resolved dynamically
    build_thinker_approval_prompt,  # noqa: F401 -- resolved dynamically
)
from src.thinktank.llm.schemas import (
    CandidateReviewResponse,
    SourceApprovalResponse,
    ThinkerApprovalResponse,
)
from src.thinktank.llm.snapshots import (
    build_candidate_review_context,  # noqa: F401 -- resolved dynamically
    build_source_approval_context,  # noqa: F401 -- resolved dynamically
    build_thinker_approval_context,  # noqa: F401 -- resolved dynamically
)
from src.thinktank.models.job import Job
from src.thinktank.models.review import LLMReview

logger = structlog.get_logger(__name__)

# Module-level singleton (safe for concurrent async use per research).
_llm_client = LLMClient()

# Valid review types for dispatch.
_VALID_REVIEW_TYPES = {"thinker_approval", "source_approval", "candidate_review"}

# Map review_type to (snapshot_builder_name, prompt_builder_name, response_schema).
# Function names are resolved at call time via _resolve_func() to support
# test patching on module-level names.
_REVIEW_TYPE_CONFIG: dict[str, tuple[str, str, type]] = {
    "thinker_approval": (
        "build_thinker_approval_context",
        "build_thinker_approval_prompt",
        ThinkerApprovalResponse,
    ),
    "source_approval": (
        "build_source_approval_context",
        "build_source_approval_prompt",
        SourceApprovalResponse,
    ),
    "candidate_review": (
        "build_candidate_review_context",
        "build_candidate_review_prompt",
        CandidateReviewResponse,
    ),
}


def _resolve_func(name: str):
    """Resolve a function by name from this module's current namespace.

    This allows test patches on module-level names to take effect,
    rather than using stale references captured at import time.
    """
    return getattr(sys.modules[__name__], name)


async def handle_llm_approval_check(session: AsyncSession, job: Job) -> None:
    """Handle an llm_approval_check job by dispatching to the correct review flow.

    Builds context, calls the LLM, creates an LLMReview audit trail row,
    and applies the decision to the target entity.

    Args:
        session: Active database session.
        job: The llm_approval_check job with payload containing review_type
             and target_id (or candidate_ids for batch review).

    Raises:
        ValueError: If review_type or target_id is missing/unknown.
        anthropic exceptions: Propagated for worker loop categorization and retry.
    """
    # 1. Extract review_type and validate
    review_type = job.payload.get("review_type")
    if not review_type:
        raise ValueError("Missing review_type in job payload")

    if review_type not in _VALID_REVIEW_TYPES:
        raise ValueError(f"Unknown review_type: {review_type}")

    # 2. Extract target_id (required for thinker/source, optional for candidate batch)
    raw_target_id = job.payload.get("target_id") or job.payload.get("entity_id")
    if not raw_target_id and review_type != "candidate_review":
        raise ValueError("Missing target_id in job payload")

    target_id = uuid.UUID(raw_target_id) if raw_target_id else None

    # 3. Look up dispatch triple (resolve at call time for testability)
    snapshot_name, prompt_name, response_schema = _REVIEW_TYPE_CONFIG[review_type]
    snapshot_builder = _resolve_func(snapshot_name)
    prompt_builder = _resolve_func(prompt_name)

    # 4. Build context
    if review_type == "candidate_review":
        raw_candidate_ids = job.payload.get("candidate_ids")
        candidate_ids = (
            [uuid.UUID(cid) for cid in raw_candidate_ids]
            if raw_candidate_ids
            else None
        )
        context = await snapshot_builder(session, candidate_ids=candidate_ids)
    else:
        context = await snapshot_builder(session, target_id)

    # 5. Build prompts
    system_prompt, user_prompt = prompt_builder(context)

    # 6. Call LLM (exceptions propagate for worker loop retry)
    result, tokens_used, duration_ms = await _llm_client.review(
        system_prompt, user_prompt, response_schema, session=session
    )

    # 7. Create LLMReview audit trail row
    review = LLMReview(
        id=uuid.uuid4(),
        review_type=review_type,
        trigger="job_gate",
        context_snapshot=context,
        prompt_used=f"{system_prompt}\n\n{user_prompt}",
        llm_response=result.model_dump_json(),
        decision=result.decision,
        decision_reasoning=result.reasoning,
        modifications=getattr(result, "modifications", None),
        flagged_items=getattr(result, "flagged_items", None),
        model=_llm_client.model,
        tokens_used=tokens_used,
        duration_ms=duration_ms,
    )
    session.add(review)
    await session.flush()

    # 8. Extract pending_job_id (optional)
    raw_pending_job_id = job.payload.get("pending_job_id")
    pending_job_id = uuid.UUID(raw_pending_job_id) if raw_pending_job_id else None

    # 9. Apply decision
    await apply_decision(
        session, review_type, target_id, pending_job_id, result, review.id
    )

    # 10. Commit
    await session.commit()

    # 11. Log completion
    logger.info(
        "llm_approval_completed",
        review_type=review_type,
        target_id=str(target_id) if target_id else None,
        decision=result.decision,
        tokens_used=tokens_used,
    )
