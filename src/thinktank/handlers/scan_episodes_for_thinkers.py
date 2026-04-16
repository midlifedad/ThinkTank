"""Handler: scan_episodes_for_thinkers -- Episode scanning and promotion.

Scans cataloged episodes for thinker name matches and promotes matching
episodes from 'cataloged' to 'pending' status. Creates ContentThinker
attribution rows linking content to matched thinkers.

Promotion rules:
    - Host-owned sources: ALL cataloged episodes promoted (role='primary', confidence=10)
    - Guest sources: Only episodes matching thinker names in title/description
      or podcast:person tags are promoted

This is the central scanning engine that determines which cataloged episodes
are worth transcribing based on thinker relevance.

Spec reference: Phase 13 -- Efficient episode cataloging.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from thinktank.ingestion.name_matcher import match_thinkers_in_text
from thinktank.ingestion.podcast_person_parser import extract_podcast_persons
from thinktank.models.content import Content, ContentThinker
from thinktank.models.job import Job
from thinktank.models.source import Source, SourceThinker
from thinktank.models.thinker import Thinker

logger = structlog.get_logger(__name__)


def _now() -> datetime:
    """Return current UTC time as timezone-aware datetime (TIMESTAMPTZ)."""
    return datetime.now(UTC)


async def handle_scan_episodes_for_thinkers(
    session: AsyncSession, job: Job
) -> None:
    """Scan cataloged episodes for thinker matches and promote to pending.

    Job payload schema:
        {
            "content_ids": ["uuid-str", ...],
            "source_id": "uuid-str",
            "descriptions": {"content-id-str": "description text", ...},
            "raw_xml": "optional - raw RSS XML for podcast:person extraction"
        }

    Args:
        session: Active database session.
        job: The scan_episodes_for_thinkers job with payload.
    """
    # a. Extract payload fields
    content_ids = job.payload.get("content_ids", [])
    source_id_str = job.payload.get("source_id")
    descriptions = job.payload.get("descriptions", {})
    raw_xml = job.payload.get("raw_xml")

    log = logger.bind(
        job_id=str(job.id),
        source_id=source_id_str,
        content_count=len(content_ids),
    )

    if not content_ids or not source_id_str:
        log.warning("scan_episodes_empty_payload")
        return

    source_id = uuid.UUID(source_id_str)

    # b. Load source
    source = await session.get(Source, source_id)
    if source is None:
        log.warning("scan_episodes_source_not_found", source_id=source_id_str)
        return

    # c. Determine if host-owned source
    host_result = await session.execute(
        select(SourceThinker).where(
            SourceThinker.source_id == source_id,
            SourceThinker.relationship_type == "host",
        )
    )
    host_source_thinkers = host_result.scalars().all()
    is_host_source = len(host_source_thinkers) > 0
    host_thinker_ids = [st.thinker_id for st in host_source_thinkers]

    # Also get host thinker name for name_matcher source_owner_name param
    source_owner_name: str | None = None
    if host_source_thinkers:
        host_thinker = await session.get(Thinker, host_thinker_ids[0])
        if host_thinker:
            source_owner_name = host_thinker.name

    # d. Load all active approved thinkers
    thinker_result = await session.execute(
        select(Thinker).where(
            Thinker.active == True,  # noqa: E712
            Thinker.approval_status == "approved",
        )
    )
    thinkers = thinker_result.scalars().all()
    thinker_names = [{"id": t.id, "name": t.name} for t in thinkers]

    # e. Extract podcast:person data if raw_xml provided
    person_by_guid: dict[str, list[dict]] = {}
    if raw_xml:
        person_by_guid = extract_podcast_persons(raw_xml)

    # f. Process each content item
    promoted_count = 0
    now = _now()

    for content_id_str in content_ids:
        content_id = uuid.UUID(content_id_str)
        content = await session.get(Content, content_id)

        # Skip missing or non-cataloged content
        if content is None or content.status != "cataloged":
            continue

        if is_host_source:
            # Host-owned sources: promote ALL cataloged episodes
            content.status = "pending"
            for host_tid in host_thinker_ids:
                existing = await session.get(
                    ContentThinker, (content.id, host_tid)
                )
                if existing is None:
                    session.add(
                        ContentThinker(
                            content_id=content.id,
                            thinker_id=host_tid,
                            role="primary",
                            confidence=10,
                            added_at=now,
                        )
                    )
            promoted_count += 1
            continue

        # Guest sources: match thinker names in title/description
        description = descriptions.get(content_id_str, "")
        matches = match_thinkers_in_text(
            content.title, description, thinker_names, source_owner_name
        )

        # Also check podcast:person data for additional matches
        # Find the content's GUID-based person data (try content URL or title as key)
        # Since we may not have the GUID stored on content, check all persons
        # against thinker names for high-confidence matches
        if person_by_guid:
            thinker_name_lower_map = {
                t["name"].lower(): t["id"] for t in thinker_names
            }
            matched_ids_from_text = {m["thinker_id"] for m in matches}

            for _guid, persons in person_by_guid.items():
                for person in persons:
                    person_name_lower = person["name"].lower()
                    if person_name_lower in thinker_name_lower_map:
                        tid = thinker_name_lower_map[person_name_lower]
                        if tid not in matched_ids_from_text:
                            matches.append({
                                "thinker_id": tid,
                                "role": "guest",
                                "confidence": 10,
                            })
                            matched_ids_from_text.add(tid)

        if matches:
            content.status = "pending"
            for match in matches:
                thinker_id = match["thinker_id"]
                existing = await session.get(
                    ContentThinker, (content.id, thinker_id)
                )
                if existing is None:
                    session.add(
                        ContentThinker(
                            content_id=content.id,
                            thinker_id=thinker_id,
                            role=match["role"],
                            confidence=match["confidence"],
                            added_at=now,
                        )
                    )
            promoted_count += 1
        # If no matches: content stays "cataloged" (no action needed)

    # g. Commit all changes
    await session.commit()

    # h. Log summary
    log.info(
        "scan_episodes_for_thinkers_complete",
        promoted_count=promoted_count,
        total_scanned=len(content_ids),
        is_host_source=is_host_source,
    )
