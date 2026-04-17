"""Daily candidate quota tracking and cascade pause logic.

Manages the max_candidates_per_day limit to prevent unbounded candidate
growth. Provides helpers to check quota status and trigger LLM review
when the queue is approaching capacity.

Spec reference: Section 5.3 (scan_for_candidates), Section 8.2 (quota_check).
"""

from datetime import UTC, datetime

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from thinktank.ingestion.config_reader import get_config_value
from thinktank.models.candidate import CandidateThinker

# Advisory lock key for serializing the daily candidate quota check+insert
# flow across concurrent scan_for_candidates handlers.
# Sources: INTEGRATIONS-REVIEW H-02, HANDLERS-REVIEW ME-04.
# Arbitrary positive 32-bit int; different from any other advisory lock.
_CANDIDATE_QUOTA_LOCK_KEY = 0x7A6B0001


async def check_daily_quota(session: AsyncSession) -> tuple[bool, int, int]:
    """Check if daily candidate quota allows more candidates.

    Reads max_candidates_per_day from system_config and counts
    candidates created today (first_seen_at >= midnight).

    Takes a transaction-scoped advisory lock so that concurrent callers
    serialize. Without this, two concurrent scan_for_candidates handlers
    can both see count=N-1, both insert one candidate, and commit N+1
    (exceeding daily_limit N). The lock releases automatically at the
    caller's commit/rollback.

    Args:
        session: Async database session.

    Returns:
        Tuple of (can_continue, candidates_today, daily_limit) where
        can_continue is True if candidates_today < daily_limit.
    """
    # Serialize the check+insert critical section. The caller's subsequent
    # candidate inserts + commit happen inside the same transaction; the
    # lock is held until that commit, so the next waiter's count reflects
    # this caller's inserts.
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:k)"),
        {"k": _CANDIDATE_QUOTA_LOCK_KEY},
    )

    daily_limit_raw = await get_config_value(session, "max_candidates_per_day", 20)
    # JSONB values may be stored as {"value": N} or as a raw int.
    if isinstance(daily_limit_raw, dict):
        daily_limit = int(daily_limit_raw.get("value", 20))
    else:
        daily_limit = int(daily_limit_raw)

    # Timezone-aware midnight UTC — first_seen_at is TIMESTAMPTZ.
    today_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)

    result = await session.execute(
        select(func.count()).select_from(CandidateThinker).where(CandidateThinker.first_seen_at >= today_start)
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
        select(func.count()).select_from(CandidateThinker).where(CandidateThinker.status == "pending_llm")
    )
    return result.scalar_one_or_none() or 0
