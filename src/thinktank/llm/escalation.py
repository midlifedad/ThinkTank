"""Timeout escalation logic for awaiting_llm jobs.

When the Anthropic API is unavailable for longer than llm_timeout_hours,
jobs stuck in awaiting_llm are flagged with needs_human_review and an
escalation LLMReview is created for audit trail.

Spec reference: Section 8.6 (fallback and escalation).
"""

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from thinktank.ingestion.config_reader import get_config_value
from thinktank.models.review import LLMReview

logger = structlog.get_logger(__name__)


async def escalate_timed_out_reviews(session: AsyncSession) -> int:
    """Escalate awaiting_llm jobs that have exceeded the timeout.

    Finds jobs in awaiting_llm status older than llm_timeout_hours,
    sets their payload.needs_human_review flag to true, and creates
    an LLMReview row for each escalation.

    Args:
        session: Active database session.

    Returns:
        Count of escalated jobs.
    """
    timeout_hours = await get_config_value(session, "llm_timeout_hours", 2)

    # Bulk UPDATE matching the raw SQL pattern from reclaim.py
    stmt = text("""
        UPDATE jobs
        SET payload = jsonb_set(
            COALESCE(payload, '{}'::jsonb),
            '{needs_human_review}',
            'true'::jsonb
        )
        WHERE status = 'awaiting_llm'
          AND created_at < LOCALTIMESTAMP - MAKE_INTERVAL(hours => :timeout_hours)
          AND NOT COALESCE((payload->>'needs_human_review')::boolean, false)
        RETURNING id, job_type
    """)

    result = await session.execute(stmt, {"timeout_hours": timeout_hours})
    escalated_rows = result.fetchall()

    for row in escalated_rows:
        job_id, job_type = row[0], row[1]
        review = LLMReview(
            review_type="timeout_escalation",
            trigger="scheduled",
            context_snapshot={"job_id": str(job_id), "job_type": job_type},
            prompt_used="N/A - timeout escalation",
            decision="escalate_to_human",
            decision_reasoning=(
                f"LLM API unavailable for >{timeout_hours}h. "
                "Escalated to human review."
            ),
        )
        session.add(review)

    await session.flush()

    count = len(escalated_rows)
    if count > 0:
        logger.info(
            "llm_timeout_escalation",
            escalated_count=count,
            timeout_hours=timeout_hours,
        )

    return count
