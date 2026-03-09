"""Backpressure priority demotion via queue depth check.

Spec reference: Section 5.8.
When the process_content queue exceeds max_pending_transcriptions,
discovery/fetch job priorities are demoted by +3 to slow ingestion.
Restores at 80% threshold (hysteresis band prevents oscillation).
"""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.thinktank.models.config_table import SystemConfig
from src.thinktank.models.job import Job

# All 10 discovery/fetch job types from spec Section 6.
# These are the types subject to backpressure demotion.
BACKPRESSURE_JOB_TYPES: set[str] = {
    "discover_thinker",
    "refresh_due_sources",
    "fetch_podcast_feed",
    "scrape_substack",
    "fetch_youtube_channel",
    "fetch_guest_feed",
    "discover_guests_listennotes",
    "discover_guests_podcastindex",
    "search_youtube_appearances",
    "scan_for_candidates",
}

# Default threshold when system_config is missing
_DEFAULT_MAX_PENDING = 500

# Demotion amount
_DEMOTION = 3

# Maximum priority value (lowest priority)
_MAX_PRIORITY = 10

# Hysteresis restore ratio (80% of threshold)
_RESTORE_RATIO = 0.8


async def get_queue_depth(session: AsyncSession, job_type: str) -> int:
    """Count pending + retrying jobs for a given job type.

    Args:
        session: Async database session.
        job_type: The job type to count.

    Returns:
        Number of jobs in 'pending' or 'retrying' status.
    """
    stmt = (
        select(func.count())
        .select_from(Job)
        .where(
            Job.job_type == job_type,
            Job.status.in_(["pending", "retrying"]),
        )
    )
    result = await session.execute(stmt)
    return result.scalar_one()


async def _get_threshold(session: AsyncSession) -> int:
    """Read max_pending_transcriptions from system_config.

    Returns:
        The threshold value, or _DEFAULT_MAX_PENDING if not configured.
    """
    stmt = select(SystemConfig.value).where(
        SystemConfig.key == "max_pending_transcriptions"
    )
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()

    if row is None:
        return _DEFAULT_MAX_PENDING

    # Handle JSONB: could be {"value": 500} or raw int
    if isinstance(row, dict):
        return int(row.get("value", _DEFAULT_MAX_PENDING))
    return int(row)


async def get_effective_priority(session: AsyncSession, job: Job) -> int:
    """Apply backpressure demotion if transcription queue is deep.

    Only applies to discovery/fetch job types (BACKPRESSURE_JOB_TYPES).
    When process_content queue depth exceeds the threshold, demotes
    priority by +3 (capped at 10). Restores when depth drops below
    80% of threshold. In the hysteresis band (80-100%), returns
    the job's current priority unchanged.

    Args:
        session: Async database session.
        job: The job to evaluate.

    Returns:
        The effective priority for this job.
    """
    if job.job_type not in BACKPRESSURE_JOB_TYPES:
        return job.priority

    depth = await get_queue_depth(session, "process_content")
    threshold = await _get_threshold(session)

    if depth > threshold:
        # Demote by +3, cap at 10 (lowest priority)
        return min(job.priority + _DEMOTION, _MAX_PRIORITY)
    elif depth < threshold * _RESTORE_RATIO:
        # Below 80% threshold: normal priority
        return job.priority
    else:
        # In the hysteresis band: maintain current priority
        return job.priority
