"""Handler: fetch_podcast_feed -- RSS feed polling, dedup, filtering, content insertion.

Fetches an RSS feed for a single source, parses episodes, applies 3-layer
dedup (URL normalization + fingerprint + incremental date check), filters
by duration/title, inserts Content rows with status='cataloged', and enqueues
a scan_episodes_for_thinkers job for thinker detection and selective promotion.

Non-skipped episodes start as 'cataloged' rather than 'pending' -- they are
only promoted to 'pending' (and therefore queued for transcription) after the
scan handler confirms a thinker name match. This dramatically reduces
unnecessary transcription spend on guest-appearance sources.

Spec reference: Sections 5.5, 5.6, 5.7; Phase 13 catalog-then-promote.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import httpx
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from thinktank.ingestion.config_reader import (
    get_config_value,
    get_source_filter_config,
)
from thinktank.ingestion.content_filter import (
    should_skip_by_duration,
    should_skip_by_title,
)
from thinktank.ingestion.feed_parser import parse_feed
from thinktank.ingestion.fingerprint import compute_fingerprint
from thinktank.ingestion.url_normalizer import normalize_url
from thinktank.models.content import Content
from thinktank.models.job import Job
from thinktank.models.source import Source
from thinktank.models.thinker import Thinker

logger = structlog.get_logger(__name__)


def _now() -> datetime:
    """Return current UTC time as timezone-naive datetime.

    Matches the pattern from queue/claim.py -- all timestamps are
    timezone-naive per Phase 1 decision.
    """
    return datetime.now(UTC).replace(tzinfo=None)


# Default skip title patterns per spec Section 5.7
_DEFAULT_SKIP_PATTERNS = [
    "trailer",
    "best of",
    "announcement",
    "teaser",
    "coming soon",
    "rebroadcast",
]


async def handle_fetch_podcast_feed(
    session: AsyncSession, job: Job
) -> None:
    """Fetch and parse a podcast RSS feed, inserting new content rows.

    Pipeline:
        1. Load source from payload.source_id
        2. Verify source is approved + active
        3. Fetch feed XML via httpx
        4. Parse with feedparser
        5. For each entry: normalize URL, compute fingerprint, check dedup, filter, insert
        6. Update source.last_fetched and source.item_count
        7. Enqueue scan_episodes_for_thinkers job for new content batch

    Args:
        session: Active database session.
        job: The fetch_podcast_feed job with payload containing source_id.

    Raises:
        ValueError: If source_id missing from payload or source not found.
        httpx.TimeoutException: On HTTP timeout (worker categorizes as HTTP_TIMEOUT).
        httpx.HTTPStatusError: On non-2xx HTTP response.
    """
    # a. Extract source_id from payload
    source_id_str = job.payload.get("source_id")
    if not source_id_str:
        raise ValueError("fetch_podcast_feed job missing source_id in payload")
    source_id = uuid.UUID(source_id_str)

    log = logger.bind(source_id=str(source_id), job_id=str(job.id))

    # b. Load source
    source = await session.get(Source, source_id)
    if source is None:
        raise ValueError(f"Source not found: {source_id}")

    log = log.bind(source_name=source.name)

    # c. Verify source is approved and active
    if source.approval_status != "approved" or not source.active:
        log.warning(
            "source_not_eligible",
            approval_status=source.approval_status,
            active=source.active,
        )
        return

    # d. source.thinker_id is deprecated — thinker lookup happens via junction if needed

    # e. Read global config values
    global_min_duration = await get_config_value(
        session, "min_duration_seconds", 600
    )
    global_skip_patterns = await get_config_value(
        session, "skip_title_patterns", _DEFAULT_SKIP_PATTERNS
    )

    # f. Compute effective filter config from source overrides
    effective_min_duration, effective_skip_patterns = get_source_filter_config(
        source.config, global_min_duration, global_skip_patterns
    )

    # g. Fetch the RSS feed
    async with httpx.AsyncClient(follow_redirects=True) as client:
        response = await client.get(source.url, timeout=60.0)
        response.raise_for_status()

    # h. Parse feed
    entries = parse_feed(response.text)
    log.info("feed_parsed", entry_count=len(entries))

    # h2. Guest filtering setup: if this is a guest source, only keep episodes
    # where the thinker is mentioned in the title or description
    guest_filter_id = job.payload.get("guest_filter_thinker_id")
    guest_thinker_name: str | None = None
    guest_last_name: str | None = None
    if guest_filter_id:
        guest_thinker = await session.get(Thinker, uuid.UUID(guest_filter_id))
        if guest_thinker:
            guest_thinker_name = guest_thinker.name.lower()
            parts = guest_thinker_name.split()
            guest_last_name = parts[-1] if len(parts) > 1 else None
            log.info("guest_filter_active", guest_name=guest_thinker.name)

    # i. Determine backfill vs incremental mode
    is_backfill = not source.backfill_complete

    # j. Process each entry
    inserted_content: list[Content] = []
    descriptions: dict[str, str] = {}
    skipped_count = 0
    dedup_count = 0
    guest_filtered_count = 0

    for entry in entries:
        # Incremental mode: skip entries published before last_fetched
        if (
            not is_backfill
            and source.last_fetched is not None
            and entry.published_at is not None
            and entry.published_at <= source.last_fetched
        ):
            continue

        # Guest filter: only keep episodes mentioning the guest thinker
        if guest_thinker_name:
            title_lower = (entry.title or "").lower()
            desc_lower = (entry.description or "").lower()
            name_match = guest_thinker_name in title_lower or guest_thinker_name in desc_lower
            last_name_match = guest_last_name and (
                guest_last_name in title_lower or guest_last_name in desc_lower
            )
            if not name_match and not last_name_match:
                guest_filtered_count += 1
                continue

        # Normalize URL (Layer 1 dedup prep)
        canonical = normalize_url(entry.url)

        # Layer 1: URL dedup
        url_exists = await session.execute(
            select(Content.id).where(Content.canonical_url == canonical).limit(1)
        )
        if url_exists.scalar_one_or_none() is not None:
            dedup_count += 1
            continue

        # Compute fingerprint (Layer 2 dedup prep)
        fp = compute_fingerprint(
            entry.title, entry.published_at, entry.duration_seconds
        )

        # Layer 2: Fingerprint dedup
        if fp is not None:
            fp_exists = await session.execute(
                select(Content.id)
                .where(Content.content_fingerprint == fp)
                .limit(1)
            )
            if fp_exists.scalar_one_or_none() is not None:
                log.debug(
                    "fingerprint_alias_detected",
                    title=entry.title,
                    alias_url=entry.url,
                )
                dedup_count += 1
                continue

        # Determine status based on filtering
        if should_skip_by_duration(
            entry.duration_seconds, effective_min_duration
        ) or should_skip_by_title(entry.title, effective_skip_patterns):
            status = "skipped"
            skipped_count += 1
        else:
            status = "cataloged"

        # Insert Content row
        content = Content(
            id=uuid.uuid4(),
            source_id=source.id,
            source_owner_id=None,  # DEPRECATED — use content_thinkers junction
            content_type="episode",
            url=entry.url,
            canonical_url=canonical,
            content_fingerprint=fp,
            title=entry.title,
            published_at=entry.published_at,
            duration_seconds=entry.duration_seconds,
            show_name=entry.show_name,
            status=status,
            discovered_at=_now(),
        )
        session.add(content)
        inserted_content.append(content)

        # Collect description for attribution payload
        descriptions[str(content.id)] = entry.description or ""

    # Flush to get IDs assigned
    await session.flush()

    # k. Update source after processing
    now = _now()
    source.last_fetched = now
    source.item_count += len(inserted_content)

    if is_backfill:
        source.backfill_complete = True

    # Enqueue scan_episodes_for_thinkers job if we inserted any content
    if inserted_content:
        scan_job = Job(
            id=uuid.uuid4(),
            job_type="scan_episodes_for_thinkers",
            payload={
                "content_ids": [str(c.id) for c in inserted_content],
                "source_id": str(source_id),
                "descriptions": descriptions,
                "raw_xml": response.text,
            },
            priority=3,
            status="pending",
            attempts=0,
            max_attempts=3,
            created_at=_now(),
        )
        session.add(scan_job)

    # l. Commit everything in one transaction
    await session.commit()

    log.info(
        "fetch_complete",
        inserted=len(inserted_content),
        skipped=skipped_count,
        deduped=dedup_count,
        guest_filtered=guest_filtered_count,
        backfill=is_backfill,
    )
