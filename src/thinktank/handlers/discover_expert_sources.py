"""Handler: discover_expert_sources -- register an expert's OWNED channels.

Web-Lane Hardening W3.1. Given a thinker, discover the channels they own
(discovery/owned_sources.py: Exa + LLM identity check) and register each
as a Source with relationship_type='owns', approval-gated exactly like
guest-discovered feeds. Owned YouTube channels and podcast feeds then
flow through the existing fetch_youtube_channel / fetch_podcast_feed
ingestion once approved; website/Substack sources are registered now and
gain ingestion in W3.2.

The LLM only surfaces channels it is confident about, and the approval
gate is the final identity check -- so a namesake or fan channel that
slips through discovery still needs a human/LLM yes before it ingests.

Job payload schema: {"thinker_id": "uuid-str"}
"""

from __future__ import annotations

import uuid

import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from thinktank.discovery.owned_sources import find_owned_channels
from thinktank.ingestion.url_normalizer import normalize_url
from thinktank.models.candidate import CandidateThinker
from thinktank.models.job import Job
from thinktank.models.source import Source, SourceThinker
from thinktank.models.thinker import Thinker

logger = structlog.get_logger(__name__)

# (source_type, OwnedChannels attribute). Order = registration order.
_CHANNEL_TYPES = (
    ("youtube_channel", "youtube_channel_url"),
    ("podcast_rss", "podcast_url"),
    ("substack", "substack_url"),
    ("website", "website_url"),
)


async def handle_discover_expert_sources(session: AsyncSession, job: Job) -> None:
    """Discover + register an expert's owned content sources."""
    thinker_id = job.payload.get("thinker_id")
    if not thinker_id:
        raise ValueError("thinker_id missing from discover_expert_sources payload")
    try:
        thinker_uuid = uuid.UUID(thinker_id) if isinstance(thinker_id, str) else thinker_id
    except (ValueError, TypeError):
        logger.error("discover_expert_sources_invalid_thinker_id", thinker_id=str(thinker_id))
        return

    thinker = await session.get(Thinker, thinker_uuid)
    if thinker is None:
        logger.warning("discover_expert_sources_thinker_not_found", thinker_id=str(thinker_id))
        return

    log = logger.bind(job_id=str(job.id), thinker=thinker.slug)

    # Use the vetting search_area as identity context when available.
    area = await session.scalar(
        select(CandidateThinker.search_area).where(CandidateThinker.thinker_id == thinker.id).limit(1)
    )
    channels = await find_owned_channels(session, thinker.name, area)
    if channels is None:
        log.info("discover_expert_sources_no_channels")
        return

    registered = 0
    for source_type, attr in _CHANNEL_TYPES:
        raw_url = getattr(channels, attr, None)
        if not raw_url:
            continue
        if await _register_owned_source(session, thinker, source_type, raw_url):
            registered += 1

    await session.commit()
    log.info("discover_expert_sources_complete", registered=registered, reasoning=channels.reasoning)


async def _register_owned_source(session: AsyncSession, thinker: Thinker, source_type: str, raw_url: str) -> bool:
    """Register one owned source (idempotent by URL). True if newly created.

    Mirrors discover_guests_podcastindex: atomic upsert on the unique url
    index, junction row (relationship_type='owns'), and an approval job
    for genuinely new sources. Only ingestable types (youtube_channel,
    podcast_rss) get an approval->fetch path today; website/substack rows
    register but await W3.2 ingestion.
    """
    url = normalize_url(raw_url)
    new_id = uuid.uuid4()
    inserted_id = await session.scalar(
        pg_insert(Source)
        .values(
            id=new_id,
            source_type=source_type,
            name=f"{thinker.name} — {source_type.replace('_', ' ')}",
            url=url,
            approval_status="pending_llm",
            config={"owned_by_thinker": str(thinker.id)},
        )
        .on_conflict_do_nothing(index_elements=["url"])
        .returning(Source.id)
    )

    if inserted_id is None:
        # URL already tracked -- ensure the owns junction exists, then done.
        existing = await session.scalar(select(Source).where(Source.url == url))
        if existing is not None:
            link = await session.scalar(
                select(SourceThinker).where(
                    SourceThinker.source_id == existing.id, SourceThinker.thinker_id == thinker.id
                )
            )
            if link is None:
                session.add(SourceThinker(source_id=existing.id, thinker_id=thinker.id, relationship_type="owns"))
        return False

    session.add(SourceThinker(source_id=inserted_id, thinker_id=thinker.id, relationship_type="owns"))
    session.add(
        Job(
            id=uuid.uuid4(),
            job_type="llm_approval_check",
            payload={"review_type": "source_approval", "target_id": str(inserted_id)},
            priority=3,
            status="pending",
            attempts=0,
            max_attempts=3,
        )
    )
    return True
