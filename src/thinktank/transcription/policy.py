"""Transcription eligibility policy.

Amir directive 2026-07-11: "I don't want anything more than 5 years old
to start." Episodes older than the cutoff are not enqueued for
transcription -- they still get cataloged, scanned, promoted, and
attributed (that metadata work is free); only the paid/expensive
transcription step is gated.

The cutoff is runtime-tunable via system_config
``transcription_max_age_days``:
    - row absent      -> DEFAULT_MAX_AGE_DAYS (5 years)
    - value 0         -> unlimited (transcribe any age)
    - value N > 0     -> N days

Content with NULL published_at passes the gate (fail-open): a missing
date is a feed-parsing artifact, not evidence of age, and new episodes
are exactly the rows most likely to matter.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from thinktank.models.config_table import SystemConfig

logger = structlog.get_logger(__name__)

# 5 years. "To start" -- operators can widen or disable via system_config.
DEFAULT_MAX_AGE_DAYS = 1825


async def get_transcription_age_cutoff(session: AsyncSession) -> datetime | None:
    """Return the oldest published_at eligible for transcription.

    Returns None when age is unlimited (config value 0).
    """
    stmt = select(SystemConfig.value).where(SystemConfig.key == "transcription_max_age_days")
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()

    days: int = DEFAULT_MAX_AGE_DAYS
    if row is not None:
        value = row.get("value", DEFAULT_MAX_AGE_DAYS) if isinstance(row, dict) else row
        try:
            days = int(value)
        except (TypeError, ValueError):
            logger.warning("transcription_max_age_days_invalid", raw=str(value)[:50])
            days = DEFAULT_MAX_AGE_DAYS

    if days <= 0:
        return None
    return datetime.now(UTC) - timedelta(days=days)


def is_transcribable(published_at: datetime | None, cutoff: datetime | None) -> bool:
    """True when an episode's age passes the policy gate."""
    if cutoff is None or published_at is None:
        return True
    if published_at.tzinfo is None:
        published_at = published_at.replace(tzinfo=UTC)
    return published_at >= cutoff
