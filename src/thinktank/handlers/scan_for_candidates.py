"""Handler: scan_for_candidates -- Scan episode metadata for candidate thinkers.

Extracts person names from episode titles and descriptions using regex-based
name extraction. Creates CandidateThinker rows for new names, increments
appearance_count for existing candidates, and skips names matching known
thinkers.

Enforces daily quota limits and triggers LLM review at 80% capacity.
Pauses cascade discovery when the pending_llm queue exceeds 40.

Spec reference: Section 5.3 (scan_for_candidates), DISC-01.
"""

from __future__ import annotations

from datetime import UTC, datetime

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from thinktank.discovery.name_extractor import extract_names
from thinktank.discovery.quota import (
    check_daily_quota,
    get_pending_candidate_count,
    should_trigger_llm_review,
)
from thinktank.ingestion.trigram import find_similar_candidates, find_similar_thinkers
from thinktank.models.candidate import CandidateThinker
from thinktank.models.content import Content
from thinktank.models.job import Job

logger = structlog.get_logger(__name__)


def _now() -> datetime:
    """Return current UTC time as timezone-naive datetime."""
    return datetime.now(UTC).replace(tzinfo=None)


async def handle_scan_for_candidates(
    session: AsyncSession, job: Job
) -> None:
    """Scan episode content for candidate thinker names.

    Reads content_ids from job.payload, extracts names from each content
    item's title and body_text, and creates/updates CandidateThinker rows.

    Cascade pause: If pending_llm queue > 40, returns early.
    Daily quota: Stops creating candidates if daily limit exceeded.
    LLM review trigger: Enqueues llm_approval_check at 80% quota.

    Args:
        session: Active database session.
        job: The scan_for_candidates job with payload containing content_ids.
    """
    content_ids = job.payload.get("content_ids", [])
    log = logger.bind(job_id=str(job.id), content_count=len(content_ids))

    if not content_ids:
        log.warning("scan_for_candidates_empty_payload")
        return

    # Cascade pause: check pending_llm queue depth
    pending_count = await get_pending_candidate_count(session)
    if pending_count > 40:
        log.warning(
            "scan_for_candidates_cascade_pause",
            pending_count=pending_count,
        )
        return

    # Check daily quota
    can_continue, candidates_today, daily_limit = await check_daily_quota(session)
    if not can_continue:
        log.info(
            "scan_for_candidates_quota_exhausted",
            candidates_today=candidates_today,
            daily_limit=daily_limit,
        )
        return

    now = _now()
    candidates_created = 0
    candidates_updated = 0

    for content_id_str in content_ids:
        content = await session.get(Content, content_id_str)
        if content is None:
            log.warning("scan_for_candidates_content_not_found", content_id=content_id_str)
            continue

        names = extract_names(content.title, content.body_text or "")

        for name in names:
            # Skip names matching existing thinkers
            similar_thinkers = await find_similar_thinkers(session, name)
            if similar_thinkers:
                continue

            # Check for existing candidate match
            similar_candidates = await find_similar_candidates(session, name)
            if similar_candidates:
                # Increment appearance_count on the best match
                candidate_id = similar_candidates[0][0]
                candidate = await session.get(CandidateThinker, candidate_id)
                if candidate is not None:
                    candidate.appearance_count += 1
                    candidate.last_seen_at = now
                    candidates_updated += 1
                continue

            # Check quota before creating
            if candidates_today >= daily_limit:
                log.info("scan_for_candidates_quota_mid_batch", candidates_today=candidates_today)
                break

            # Create new candidate
            candidate = CandidateThinker(
                name=name,
                normalized_name=name,
                status="pending_llm",
                appearance_count=1,
                first_seen_at=now,
                last_seen_at=now,
            )
            session.add(candidate)
            candidates_created += 1
            candidates_today += 1

    # Trigger LLM review at 80% quota
    if should_trigger_llm_review(candidates_today, daily_limit):
        review_job = Job(
            job_type="llm_approval_check",
            payload={"review_type": "candidate_review"},
            priority=1,
            status="pending",
            attempts=0,
            max_attempts=3,
        )
        session.add(review_job)

    await session.commit()

    log.info(
        "scan_for_candidates_complete",
        candidates_created=candidates_created,
        candidates_updated=candidates_updated,
        candidates_today=candidates_today,
    )
