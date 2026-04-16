"""Handler: fetch_youtube_channel -- YouTube channel cataloging via Data API v3.

Fetches all videos from a YouTube channel's uploads playlist, applies
duration/title/category filtering, inserts Content rows with status='cataloged'
following the catalog-then-promote pattern, and enqueues scan_episodes_for_thinkers
for thinker guest detection.

Mirrors fetch_podcast_feed.py structure with YouTube-specific adaptations.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime

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
from thinktank.ingestion.fingerprint import compute_fingerprint
from thinktank.ingestion.url_normalizer import normalize_url
from thinktank.ingestion.youtube_client import SKIP_CATEGORY_IDS, YouTubeClient
from thinktank.models.content import Content
from thinktank.models.job import Job
from thinktank.models.source import Source
from thinktank.queue.rate_limiter import check_and_acquire_rate_limit

logger = structlog.get_logger(__name__)

# YouTube-specific skip title patterns (merged with global patterns)
_YOUTUBE_SKIP_PATTERNS = ["shorts", "#shorts", "highlights", "clip"]

# Regex to extract channel ID from YouTube channel URL
_CHANNEL_ID_RE = re.compile(r"youtube\.com/channel/(UC[a-zA-Z0-9_-]+)")


def _now() -> datetime:
    """Return current UTC time as timezone-naive datetime.

    Matches the pattern from queue/claim.py -- all timestamps are
    timezone-naive per Phase 1 decision.
    """
    return datetime.now(UTC).replace(tzinfo=None)


def _parse_published_at(published_at_str: str) -> datetime | None:
    """Parse YouTube ISO 8601 timestamp to timezone-naive datetime.

    Args:
        published_at_str: Timestamp string like "2024-01-15T10:00:00Z".

    Returns:
        Timezone-naive datetime, or None if parsing fails.
    """
    if not published_at_str:
        return None
    try:
        dt = datetime.fromisoformat(published_at_str.replace("Z", "+00:00"))
        return dt.replace(tzinfo=None)
    except (ValueError, TypeError):
        return None


async def handle_fetch_youtube_channel(
    session: AsyncSession, job: Job
) -> None:
    """Fetch and catalog videos from a YouTube channel.

    Pipeline:
        1. Load source from payload.source_id
        2. Verify source is approved + active
        3. Read config (min_duration, skip patterns, API key)
        4. Check rate limit
        5. Extract channel_id, create YouTubeClient, fetch all videos
        6. For each video: normalize URL, dedup, filter, insert Content
        7. Update source.last_fetched and source.item_count
        8. Enqueue scan_episodes_for_thinkers for non-skipped content
        9. Commit

    Args:
        session: Active database session.
        job: The fetch_youtube_channel job with payload containing source_id.

    Raises:
        ValueError: If source_id missing, source not found, API key not configured,
                    or rate limited.
    """
    # a. Extract source_id from payload
    source_id_str = job.payload.get("source_id")
    if not source_id_str:
        raise ValueError("fetch_youtube_channel job missing source_id in payload")
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

    # d. Read global config values
    global_min_duration = await get_config_value(
        session, "min_duration_seconds", 600
    )
    global_skip_patterns = await get_config_value(
        session, "skip_title_patterns", _YOUTUBE_SKIP_PATTERNS
    )

    # e. Compute effective filter config from source overrides
    effective_min_duration, effective_skip_patterns = get_source_filter_config(
        source.config, global_min_duration, global_skip_patterns
    )

    # Merge YouTube-specific skip patterns
    yt_patterns_set = set(p.lower() for p in _YOUTUBE_SKIP_PATTERNS)
    existing_set = set(p.lower() for p in effective_skip_patterns)
    for pattern in _YOUTUBE_SKIP_PATTERNS:
        if pattern.lower() not in existing_set:
            effective_skip_patterns.append(pattern)

    # f. Read YouTube API key
    api_key = await get_config_value(session, "youtube_api_key", "")
    if not api_key:
        raise ValueError("YouTube API key not configured in system_config")

    # g. Check rate limit
    rate_ok = await check_and_acquire_rate_limit(
        session, "youtube", str(job.id)
    )
    if not rate_ok:
        raise ValueError("YouTube API rate limited")

    # h. Extract channel_id
    channel_id = source.external_id
    if not channel_id:
        # Try to parse from source URL
        match = _CHANNEL_ID_RE.search(source.url)
        if match:
            channel_id = match.group(1)
    if not channel_id:
        raise ValueError(
            f"Cannot determine channel ID for source {source_id}: "
            f"no external_id and URL does not contain channel ID"
        )

    # i. Determine backfill vs incremental mode
    is_backfill = not source.backfill_complete

    # j. Create client and fetch videos
    client = YouTubeClient(api_key)
    videos = client.fetch_all_channel_videos(channel_id)

    log.info("videos_fetched", video_count=len(videos), quota_used=client.quota_used)

    # k. Process each video
    inserted_content: list[Content] = []
    descriptions: dict[str, str] = {}
    skipped_count = 0
    dedup_count = 0

    for video in videos:
        video_id = video["video_id"]
        title = video["title"]
        description = video.get("description", "")
        published_at = _parse_published_at(video.get("published_at", ""))
        duration_seconds = video.get("duration_seconds")
        category_id = video.get("category_id", "")

        # Generate URL and normalize
        url = f"https://www.youtube.com/watch?v={video_id}"
        canonical = normalize_url(url)

        # Layer 1: URL dedup
        url_exists = await session.execute(
            select(Content.id).where(Content.canonical_url == canonical).limit(1)
        )
        if url_exists.scalar_one_or_none() is not None:
            dedup_count += 1
            continue

        # Compute fingerprint (Layer 2 dedup)
        fp = compute_fingerprint(title, published_at, duration_seconds)

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
                    title=title,
                    alias_url=url,
                )
                dedup_count += 1
                continue

        # Determine status based on filtering
        if should_skip_by_duration(duration_seconds, effective_min_duration):
            status = "skipped"
            skipped_count += 1
        elif should_skip_by_title(title, effective_skip_patterns):
            status = "skipped"
            skipped_count += 1
        elif category_id in SKIP_CATEGORY_IDS:
            status = "skipped"
            skipped_count += 1
        else:
            status = "cataloged"

        # Insert Content row
        content = Content(
            id=uuid.uuid4(),
            source_id=source.id,
            source_owner_id=None,  # DEPRECATED -- use content_thinkers junction
            content_type="video",
            url=url,
            canonical_url=canonical,
            content_fingerprint=fp,
            title=title,
            published_at=published_at,
            duration_seconds=duration_seconds,
            show_name=source.name,
            status=status,
            discovered_at=_now(),
        )
        session.add(content)
        inserted_content.append(content)

        # Collect description for scan payload
        descriptions[str(content.id)] = description

    # Flush to get IDs assigned
    await session.flush()

    # l. Update source metadata
    now = _now()
    source.last_fetched = now
    source.item_count += len(inserted_content)

    if is_backfill:
        source.backfill_complete = True

    # m. Enqueue scan_episodes_for_thinkers for non-skipped content
    non_skipped = [c for c in inserted_content if c.status != "skipped"]
    if non_skipped:
        scan_job = Job(
            id=uuid.uuid4(),
            job_type="scan_episodes_for_thinkers",
            payload={
                "content_ids": [str(c.id) for c in non_skipped],
                "source_id": str(source_id),
                "descriptions": {
                    str(c.id): descriptions.get(str(c.id), "")
                    for c in non_skipped
                },
            },
            priority=3,
            status="pending",
            attempts=0,
            max_attempts=3,
            created_at=_now(),
        )
        session.add(scan_job)

    # n. Commit everything in one transaction
    await session.commit()

    log.info(
        "fetch_youtube_complete",
        inserted=len(inserted_content),
        skipped=skipped_count,
        deduped=dedup_count,
        backfill=is_backfill,
        quota_used=client.quota_used,
    )
