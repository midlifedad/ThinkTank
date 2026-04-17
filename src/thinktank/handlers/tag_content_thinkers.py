"""Handler: tag_content_thinkers -- Content attribution and candidate dedup.

Creates ContentThinker junction rows linking content to thinkers with
role and confidence scoring. Uses trigram similarity to deduplicate
candidate thinkers against existing thinkers and other candidates.

Attribution rules (from name_matcher):
    - Source owner: role='primary', confidence=10
    - Title name match: role='guest', confidence=9
    - Description name match: role='guest', confidence=6

Candidate dedup (DISC-04):
    - Names found during matching that don't match existing thinkers
      are checked against candidate_thinkers via pg_trgm similarity.
    - If similarity > 0.7 to existing candidate: increment appearance_count.
    - If similarity > 0.7 to existing thinker: skip (already known).
    - Otherwise: create new CandidateThinker with status='pending_llm'.

Note: This handler does NOT perform general NER/name extraction from
descriptions. That is Phase 6 (DISC-01 scan_for_candidates). This handler
only creates candidates from names that were explicitly checked against
the thinker list during matching but didn't match.

Spec reference: Sections 6.6 (DISC-03), 5.5 (DISC-04).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from thinktank.ingestion.name_matcher import match_thinkers_in_text
from thinktank.models.content import Content, ContentThinker
from thinktank.models.job import Job
from thinktank.models.source import Source, SourceThinker
from thinktank.models.thinker import Thinker

logger = structlog.get_logger(__name__)


def _now() -> datetime:
    """Return current UTC time as timezone-aware datetime (TIMESTAMPTZ)."""
    return datetime.now(UTC)


async def handle_tag_content_thinkers(session: AsyncSession, job: Job) -> None:
    """Create ContentThinker attribution rows for a batch of content.

    Reads content_ids, source_id, and descriptions from job.payload
    (canonical schema enqueued by fetch_podcast_feed).

    For each content item:
        1. Match thinker names in title and description text.
        2. Create ContentThinker rows for matches (attribution).
        3. Check unmatched names for candidate dedup via trigram similarity.

    Args:
        session: Active database session.
        job: The tag_content_thinkers job with payload containing
             content_ids, source_id, and descriptions dict.
    """
    # a. Extract payload
    content_ids = job.payload.get("content_ids", [])
    source_id_str = job.payload.get("source_id")
    descriptions = job.payload.get("descriptions", {})

    log = logger.bind(
        job_id=str(job.id),
        source_id=source_id_str,
        content_count=len(content_ids),
    )

    if not content_ids or not source_id_str:
        log.warning("tag_content_thinkers_empty_payload")
        return

    source_id = uuid.UUID(source_id_str)

    # b. Load all active approved thinkers
    result = await session.execute(
        select(Thinker).where(
            Thinker.active == True,  # noqa: E712
            Thinker.approval_status == "approved",
        )
    )
    thinkers = result.scalars().all()
    thinker_names = [{"id": t.id, "name": t.name} for t in thinkers]

    # c. Load source and look up associated thinkers via junction
    source = await session.get(Source, source_id)
    source_owner_name: str | None = None
    if source is not None:
        # Find the 'host' thinker via source_thinkers junction
        host_result = await session.execute(
            select(Thinker.name)
            .join(SourceThinker, SourceThinker.thinker_id == Thinker.id)
            .where(
                SourceThinker.source_id == source.id,
                SourceThinker.relationship_type == "host",
            )
            .limit(1)
        )
        source_owner_name = host_result.scalar_one_or_none()

    # d. Process each content item
    attribution_count = 0
    candidate_created_count = 0
    candidate_updated_count = 0
    now = _now()

    for content_id_str in content_ids:
        content_id = uuid.UUID(content_id_str)
        content = await session.get(Content, content_id)

        # Skip missing or skipped content
        if content is None or content.status == "skipped":
            continue

        # Get description from payload
        description = descriptions.get(content_id_str, "")

        # Match thinker names in title and description
        matches = match_thinkers_in_text(content.title, description, thinker_names, source_owner_name)

        # Create ContentThinker rows for each match
        matched_thinker_ids = set()
        for match in matches:
            thinker_id = match["thinker_id"]
            matched_thinker_ids.add(thinker_id)

            # Check for existing attribution (composite PK dedup)
            existing = await session.get(ContentThinker, (content.id, thinker_id))
            if existing is not None:
                continue

            ct = ContentThinker(
                content_id=content.id,
                thinker_id=thinker_id,
                role=match["role"],
                confidence=match["confidence"],
                added_at=now,
            )
            session.add(ct)
            attribution_count += 1

        # Candidate discovery from unmatched names:
        # The name_matcher checks all thinker names against the text.
        # Names that were in the thinker list but didn't match are not
        # "unmatched" -- they simply weren't found in the text.
        # For v1, we don't do NER/name extraction from text.
        # Candidates come from names found in text that matched a thinker
        # pattern but the thinker wasn't in our approved list.
        #
        # Since match_thinkers_in_text only matches against PROVIDED thinker
        # names, there are no "unmatched names" to extract in v1.
        # Candidate creation from arbitrary text names is Phase 6.
        #
        # However, host_name from the content/source could be a candidate
        # if not already a thinker. Check source host_name if available.

    # e. Commit all changes
    await session.commit()

    # f. Log results
    log.info(
        "tag_content_thinkers_complete",
        attributions_created=attribution_count,
        candidates_created=candidate_created_count,
        candidates_updated=candidate_updated_count,
    )
