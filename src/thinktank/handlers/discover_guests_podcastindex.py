"""Handler: discover_guests_podcastindex -- Discover guest appearances via Podcast Index.

Searches the Podcast Index API for episodes by person name,
and registers discovered podcast feeds as new Sources pending LLM approval.
Deduplicates by normalized URL to prevent duplicate source registration.

Spec reference: Section 5.4 (guest discovery), DISC-02.
"""

from __future__ import annotations

import uuid

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.thinktank.discovery.podcastindex_client import PodcastIndexClient
from src.thinktank.ingestion.url_normalizer import normalize_url
from src.thinktank.models.job import Job
from src.thinktank.models.source import Source, SourceThinker
from src.thinktank.models.thinker import Thinker
from src.thinktank.secrets import get_secret

logger = structlog.get_logger(__name__)


async def handle_discover_guests_podcastindex(
    session: AsyncSession, job: Job
) -> None:
    """Discover guest appearances via Podcast Index API.

    Reads thinker_id from job.payload, searches Podcast Index for episodes
    by person name, and creates Source rows for new podcast feeds.

    Args:
        session: Active database session.
        job: The discover_guests_podcastindex job with payload containing thinker_id.

    Raises:
        ValueError: If API key/secret env vars are missing, or if rate-limited.
    """
    thinker_id = job.payload.get("thinker_id")
    log = logger.bind(job_id=str(job.id), thinker_id=thinker_id)

    if not thinker_id:
        log.warning("discover_guests_podcastindex_no_thinker_id")
        return

    thinker = await session.get(Thinker, thinker_id)
    if thinker is None:
        log.warning("discover_guests_podcastindex_thinker_not_found")
        return

    api_key = await get_secret(session, "podcastindex_api_key")
    api_secret = await get_secret(session, "podcastindex_api_secret")
    if not api_key or not api_secret:
        raise ValueError("Podcast Index API key/secret not configured — set via Admin > API Keys")

    client = PodcastIndexClient(api_key, api_secret)
    data = await client.search_by_person(
        session, worker_id=str(job.id), person_name=thinker.name
    )

    if data is None:
        raise ValueError("Rate limited by Podcast Index")

    sources_created = 0
    for item in data.get("items", []):
        feed_url = item.get("feedUrl")
        if not feed_url:
            continue

        normalized = normalize_url(feed_url)

        # Check for existing source with same URL
        existing = await session.execute(
            select(Source).where(Source.url == normalized)
        )
        existing_source = existing.scalar_one_or_none()

        if existing_source is not None:
            # Source exists — ensure junction row links it to this thinker
            existing_junction = await session.execute(
                select(SourceThinker).where(
                    SourceThinker.source_id == existing_source.id,
                    SourceThinker.thinker_id == thinker.id,
                )
            )
            if existing_junction.scalar_one_or_none() is None:
                junction = SourceThinker(
                    source_id=existing_source.id,
                    thinker_id=thinker.id,
                    relationship_type="guest_appearance",
                )
                session.add(junction)
            continue

        source = Source(
            id=uuid.uuid4(),
            thinker_id=None,
            source_type="podcast_rss",
            name=item.get("feedTitle", "Unknown Podcast"),
            url=normalized,
            approval_status="pending_llm",
            config={"is_guest_source": True},
        )
        session.add(source)

        # Create junction row linking source to thinker as guest appearance
        junction = SourceThinker(
            source_id=source.id,
            thinker_id=thinker.id,
            relationship_type="guest_appearance",
        )
        session.add(junction)
        sources_created += 1

        # Create LLM approval job for the new source
        llm_job = Job(
            id=uuid.uuid4(),
            job_type="llm_approval_check",
            payload={
                "review_type": "source_approval",
                "target_id": str(source.id),
            },
            priority=3,
            status="pending",
            attempts=0,
            max_attempts=3,
        )
        session.add(llm_job)

    await session.commit()

    log.info(
        "discover_guests_podcastindex_complete",
        thinker_name=thinker.name,
        sources_created=sources_created,
    )
