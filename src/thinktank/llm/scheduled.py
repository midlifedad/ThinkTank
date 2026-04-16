"""Scheduled LLM task implementations: health check, daily digest, weekly audit.

Each function builds a bounded context snapshot, calls the LLM via the
shared client, logs the result as an LLMReview row, and returns the review.
All functions catch exceptions gracefully to avoid crashing the scheduler.

Spec reference: Section 8.2 (scheduled check track).
"""

import structlog
from sqlalchemy.ext.asyncio import AsyncSession
from thinktank.llm.client import LLMClient
from thinktank.llm.prompts import (
    build_daily_digest_prompt,
    build_health_check_prompt,
    build_weekly_audit_prompt,
)
from thinktank.llm.schemas import (
    DailyDigestResponse,
    HealthCheckResponse,
    WeeklyAuditResponse,
)
from thinktank.llm.snapshots import (
    build_daily_digest_context,
    build_health_check_context,
    build_weekly_audit_context,
)
from thinktank.models.review import LLMReview

logger = structlog.get_logger(__name__)

# Module-level singleton for scheduled LLM calls
_llm_client = LLMClient()


async def run_health_check(session: AsyncSession) -> LLMReview | None:
    """Run a system health check via LLM and log the result.

    Builds a bounded context snapshot of recent jobs, errors, source health,
    and queue depth. The LLM evaluates system health and may suggest config
    adjustments.

    Args:
        session: Active database session.

    Returns:
        The created LLMReview row, or None if the LLM call failed.
    """
    try:
        context = await build_health_check_context(session)
        system_prompt, user_prompt = build_health_check_prompt(context)

        result, tokens, duration = await _llm_client.review(
            system_prompt, user_prompt, HealthCheckResponse
        )

        review = LLMReview(
            review_type="health_check",
            trigger="scheduled",
            context_snapshot=context,
            prompt_used=f"SYSTEM: {system_prompt[:200]}...\nUSER: {user_prompt[:200]}...",
            llm_response=result.model_dump_json(),
            decision=result.status,
            decision_reasoning="; ".join(result.findings),
            model=_llm_client.model,
            tokens_used=tokens,
            duration_ms=duration,
        )
        session.add(review)
        await session.flush()

        if result.config_adjustments:
            logger.info(
                "health_check_config_adjustments",
                adjustments=result.config_adjustments,
            )

        logger.info(
            "health_check_complete",
            status=result.status,
            findings_count=len(result.findings),
            tokens_used=tokens,
            duration_ms=duration,
        )

        return review

    except Exception:
        logger.exception("health_check_failed")
        return None


async def run_daily_digest(session: AsyncSession) -> LLMReview | None:
    """Run a daily digest via LLM and log the result.

    Summarizes the last 24 hours of content ingestion, thinker activity,
    and system health.

    Args:
        session: Active database session.

    Returns:
        The created LLMReview row, or None if the LLM call failed.
    """
    try:
        context = await build_daily_digest_context(session)
        system_prompt, user_prompt = build_daily_digest_prompt(context)

        result, tokens, duration = await _llm_client.review(
            system_prompt, user_prompt, DailyDigestResponse
        )

        review = LLMReview(
            review_type="daily_digest",
            trigger="scheduled",
            context_snapshot=context,
            prompt_used=f"SYSTEM: {system_prompt[:200]}...\nUSER: {user_prompt[:200]}...",
            llm_response=result.model_dump_json(),
            decision="digest_generated",
            decision_reasoning=result.summary,
            model=_llm_client.model,
            tokens_used=tokens,
            duration_ms=duration,
        )
        session.add(review)
        await session.flush()

        logger.info(
            "daily_digest_complete",
            highlights_count=len(result.highlights),
            tokens_used=tokens,
            duration_ms=duration,
        )

        return review

    except Exception:
        logger.exception("daily_digest_failed")
        return None


async def run_weekly_audit(session: AsyncSession) -> LLMReview | None:
    """Run a weekly audit via LLM and log the result.

    Evaluates weekly growth, inactive thinkers, error sources, and
    provides structural observations about the corpus.

    Args:
        session: Active database session.

    Returns:
        The created LLMReview row, or None if the LLM call failed.
    """
    try:
        context = await build_weekly_audit_context(session)
        system_prompt, user_prompt = build_weekly_audit_prompt(context)

        result, tokens, duration = await _llm_client.review(
            system_prompt, user_prompt, WeeklyAuditResponse
        )

        review = LLMReview(
            review_type="weekly_audit",
            trigger="scheduled",
            context_snapshot=context,
            prompt_used=f"SYSTEM: {system_prompt[:200]}...\nUSER: {user_prompt[:200]}...",
            llm_response=result.model_dump_json(),
            decision="audit_complete",
            decision_reasoning=result.summary,
            model=_llm_client.model,
            tokens_used=tokens,
            duration_ms=duration,
        )
        session.add(review)
        await session.flush()

        if result.thinkers_to_deactivate:
            logger.info(
                "weekly_audit_deactivation_recommendations",
                thinkers=result.thinkers_to_deactivate,
            )

        if result.sources_to_retire:
            logger.info(
                "weekly_audit_retirement_recommendations",
                sources=result.sources_to_retire,
            )

        logger.info(
            "weekly_audit_complete",
            summary=result.summary[:100],
            tokens_used=tokens,
            duration_ms=duration,
        )

        return review

    except Exception:
        logger.exception("weekly_audit_failed")
        return None
