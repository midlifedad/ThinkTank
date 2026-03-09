"""Daily candidate quota tracking and cascade pause logic.

Manages the max_candidates_per_day limit to prevent unbounded candidate
growth. Provides helpers to check quota status and trigger LLM review
when the queue is approaching capacity.

Spec reference: Section 5.3 (scan_for_candidates), Section 8.2 (quota_check).
"""

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.thinktank.ingestion.config_reader import get_config_value
from src.thinktank.models.candidate import CandidateThinker


async def check_daily_quota(session: AsyncSession) -> tuple[bool, int, int]:
    """Check if daily candidate quota allows more candidates.

    Reads max_candidates_per_day from system_config and counts
    candidates created today (first_seen_at >= midnight).

    Args:
        session: Async database session.

    Returns:
        Tuple of (can_continue, candidates_today, daily_limit) where
        can_continue is True if candidates_today < daily_limit.
    """
    daily_limit = await get_config_value(session, "max_candidates_per_day", 20)

    # Timezone-naive datetime per project convention.
    today_start = datetime.now(UTC).replace(
        hour=0, minute=0, second=0, microsecond=0, tzinfo=None
    )

    result = await session.execute(
        select(func.count())
        .select_from(CandidateThinker)
        .where(CandidateThinker.first_seen_at >= today_start)
    )
    candidates_today = result.scalar_one_or_none() or 0

    can_continue = candidates_today < daily_limit
    return can_continue, candidates_today, daily_limit


def should_trigger_llm_review(candidates_today: int, daily_limit: int) -> bool:
    """Check if LLM review should be triggered based on quota usage.

    Triggers at 80% of daily limit to give the LLM time to review
    the candidate queue before it fills up.

    Args:
        candidates_today: Number of candidates created today.
        daily_limit: Maximum candidates allowed per day.

    Returns:
        True if candidates_today >= 80% of daily_limit.
    """
    return candidates_today >= int(daily_limit * 0.8)


async def get_pending_candidate_count(session: AsyncSession) -> int:
    """Count candidates with status 'pending_llm'.

    Used by handlers to check if the LLM review queue is backed up.
    When count > 40 (2x batch size), discovery should pause.

    Args:
        session: Async database session.

    Returns:
        Number of candidates awaiting LLM review.
    """
    result = await session.execute(
        select(func.count())
        .select_from(CandidateThinker)
        .where(CandidateThinker.status == "pending_llm")
    )
    return result.scalar_one_or_none() or 0
