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
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from thinktank.discovery.podcastindex_client import PodcastIndexClient
from thinktank.ingestion.url_normalizer import normalize_url
from thinktank.models.job import Job
from thinktank.models.source import Source, SourceThinker
from thinktank.models.thinker import Thinker
from thinktank.secrets import get_secret

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

    # Coerce payload thinker_id to UUID. A malformed string would raise
    # asyncpg DataError inside session.get() and fail the job with an
    # opaque error. Early-return cleanly instead.
    try:
        thinker_uuid = (
            uuid.UUID(thinker_id) if isinstance(thinker_id, str) else thinker_id
        )
    except (ValueError, TypeError, AttributeError):
        log.error("discover_guests_podcastindex_invalid_thinker_id")
        return

    thinker = await session.get(Thinker, thinker_uuid)
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

        # Atomic upsert: ON CONFLICT on the unique sources.url index.
        # Concurrent discovery jobs for the same feed would otherwise both
        # SELECT None, both INSERT, and one would die on unique violation
        # (INTEGRATIONS-REVIEW H-03). RETURNING id yields the row only when
        # we actually inserted; on conflict we refetch the existing row.
        new_source_id = uuid.uuid4()
        insert_stmt = (
            pg_insert(Source)
            .values(
                id=new_source_id,
                thinker_id=None,
                source_type="podcast_rss",
                name=item.get("feedTitle", "Unknown Podcast"),
                url=normalized,
                approval_status="pending_llm",
                config={"is_guest_source": True},
            )
            .on_conflict_do_nothing(index_elements=["url"])
            .returning(Source.id)
        )
        insert_result = await session.execute(insert_stmt)
        inserted_id = insert_result.scalar_one_or_none()

        if inserted_id is None:
            # Another worker already inserted this URL — ensure the junction
            # row links it to this thinker, then move on.
            existing = await session.execute(
                select(Source).where(Source.url == normalized)
            )
            existing_source = existing.scalar_one()
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

        # We inserted a new source — create junction + approval job.
        junction = SourceThinker(
            source_id=inserted_id,
            thinker_id=thinker.id,
            relationship_type="guest_appearance",
        )
        session.add(junction)
        sources_created += 1

        llm_job = Job(
            id=uuid.uuid4(),
            job_type="llm_approval_check",
            payload={
                "review_type": "source_approval",
                "target_id": str(inserted_id),
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
