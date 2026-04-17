"""Handler: rescan_cataloged_for_thinker -- Retroactive episode scanning.

When a new thinker is approved, scans ALL cataloged episodes to find
title matches and promotes them to 'pending' status for transcription.

This enables retroactive discovery: episodes that were cataloged before
a thinker was tracked can be promoted once the thinker is approved.

Matching:
    - Case-insensitive ILIKE on Content.title only
    - Content.description is not stored on the model; descriptions
      are only available at fetch time via job payloads
    - Retroactive match confidence = 7 (lower than real-time title match of 9)

Spec reference: Phase 13 -- Efficient episode cataloging.
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
from thinktank.models.thinker import Thinker

logger = structlog.get_logger(__name__)


def _now() -> datetime:
    """Return current UTC time as timezone-aware datetime (TIMESTAMPTZ)."""
    return datetime.now(UTC)


async def handle_rescan_cataloged_for_thinker(
    session: AsyncSession, job: Job
) -> None:
    """Rescan cataloged episodes for a newly-approved thinker.

    Job payload schema:
        {
            "thinker_id": "uuid-str",
            "thinker_name": "Sam Harris"
        }

    Args:
        session: Active database session.
        job: The rescan_cataloged_for_thinker job with payload.
    """
    thinker_id_str = job.payload.get("thinker_id")
    thinker_name = job.payload.get("thinker_name", "")

    log = logger.bind(
        job_id=str(job.id),
        thinker_id=thinker_id_str,
        thinker_name=thinker_name,
    )

    if not thinker_id_str or not thinker_name:
        log.warning("rescan_cataloged_empty_payload")
        return

    thinker_id = uuid.UUID(thinker_id_str)

    # Verify thinker exists and is approved
    thinker = await session.get(Thinker, thinker_id)
    if thinker is None:
        log.warning("rescan_cataloged_thinker_not_found")
        return

    if thinker.approval_status != "approved":
        log.warning(
            "rescan_cataloged_thinker_not_approved",
            approval_status=thinker.approval_status,
        )
        return

    # Pre-filter via ILIKE to avoid scanning every cataloged row, then confirm
    # each candidate with the shared word-boundary matcher so substrings like
    # "Scam Harrison" don't poison rescan results (HANDLERS-REVIEW ME-01).
    stmt = select(Content).where(
        Content.status == "cataloged",
        Content.title.ilike(f"%{thinker_name}%"),
    )
    result = await session.execute(stmt)
    candidates = result.scalars().all()

    thinker_names_arg = [{"id": thinker_id, "name": thinker_name}]
    matching_content = [
        c
        for c in candidates
        if match_thinkers_in_text(
            title=c.title or "",
            description="",
            thinker_names=thinker_names_arg,
            source_owner_name=None,
        )
    ]

    promoted_count = 0
    now = _now()

    for content in matching_content:
        # Check if attribution already exists
        existing = await session.get(ContentThinker, (content.id, thinker_id))
        if existing is not None:
            continue

        # Promote to pending
        content.status = "pending"

        # Create attribution with retroactive confidence
        session.add(
            ContentThinker(
                content_id=content.id,
                thinker_id=thinker_id,
                role="guest",
                confidence=7,
                added_at=now,
            )
        )
        promoted_count += 1

    await session.commit()

    log.info(
        "rescan_cataloged_for_thinker_complete",
        promoted_count=promoted_count,
        candidates_scanned=len(candidates),
        matched_after_word_boundary=len(matching_content),
        thinker_name=thinker_name,
    )
