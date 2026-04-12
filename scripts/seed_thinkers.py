"""Seed initial thinkers with LLM approval jobs and known podcast sources.

Each thinker is created with approval_status="pending_llm" and a corresponding
llm_approval_check job is enqueued for LLM review. Thinkers who host their own
podcasts get pre-approved Source rows so the pipeline can start fetching
immediately after thinker approval.

Uses ON CONFLICT DO UPDATE for idempotent thinker upserts and checks for
existing jobs to avoid duplicates.

Usage:
    python -m scripts.seed_thinkers
"""

import asyncio
import uuid

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.thinktank.models.job import Job
from src.thinktank.models.source import Source
from src.thinktank.models.thinker import Thinker

INITIAL_THINKERS = [
    {
        "name": "Lex Fridman",
        "slug": "lex-fridman",
        "tier": 1,
        "bio": "MIT AI researcher and podcast host",
        "approved_source_types": ["podcast_rss"],
        "approved_backfill_days": 365,
        "sources": [
            {
                "name": "Lex Fridman Podcast",
                "url": "https://lexfridman.com/feed/podcast/",
                "source_type": "podcast_rss",
                "refresh_interval_hours": 6,
                "approved_backfill_days": 365,
            },
        ],
    },
    {
        "name": "Andrew Huberman",
        "slug": "andrew-huberman",
        "tier": 1,
        "bio": "Stanford neuroscientist",
        "approved_source_types": ["podcast_rss"],
        "approved_backfill_days": 365,
        "sources": [
            {
                "name": "Huberman Lab",
                "url": "https://feeds.megaphone.fm/hubermanlab",
                "source_type": "podcast_rss",
                "refresh_interval_hours": 6,
                "approved_backfill_days": 365,
            },
        ],
    },
    {
        "name": "Balaji Srinivasan",
        "slug": "balaji-srinivasan",
        "tier": 2,
        "bio": "Technology entrepreneur and author",
        "approved_source_types": ["podcast_rss"],
        "approved_backfill_days": 180,
        "sources": [],
    },
    {
        "name": "Tyler Cowen",
        "slug": "tyler-cowen",
        "tier": 2,
        "bio": "Economist and author of Marginal Revolution",
        "approved_source_types": ["podcast_rss"],
        "approved_backfill_days": 180,
        "sources": [
            {
                "name": "Conversations with Tyler",
                "url": "https://feeds.megaphone.fm/conversations-with-tyler",
                "source_type": "podcast_rss",
                "refresh_interval_hours": 24,
                "approved_backfill_days": 180,
            },
        ],
    },
    {
        "name": "Joscha Bach",
        "slug": "joscha-bach",
        "tier": 2,
        "bio": "AI researcher and cognitive scientist",
        "approved_source_types": ["podcast_rss"],
        "approved_backfill_days": 180,
        "sources": [],
    },
]


async def seed_thinkers(session: AsyncSession) -> int:
    """Seed initial thinkers with LLM approval jobs and known sources.

    For each thinker:
    1. Upsert the thinker row (ON CONFLICT DO UPDATE on slug)
    2. Create pre-approved Source rows for known podcasts (idempotent by URL)
    3. Check if an llm_approval_check job already exists for this thinker
    4. If not, create one to trigger LLM review

    Returns the number of thinkers seeded.
    """
    count = 0

    for entry in INITIAL_THINKERS:
        thinker_id = uuid.uuid4()

        # Upsert thinker
        stmt = insert(Thinker).values(
            id=thinker_id,
            name=entry["name"],
            slug=entry["slug"],
            tier=entry["tier"],
            bio=entry["bio"],
            approval_status="pending_llm",
            approved_source_types=entry.get("approved_source_types"),
            approved_backfill_days=entry.get("approved_backfill_days"),
        ).on_conflict_do_update(
            index_elements=["slug"],
            set_={
                "name": entry["name"],
                "tier": entry["tier"],
                "bio": entry["bio"],
                "approved_source_types": entry.get("approved_source_types"),
                "approved_backfill_days": entry.get("approved_backfill_days"),
            },
        ).returning(Thinker.id)

        result = await session.execute(stmt)
        actual_id = result.scalar_one()
        count += 1

        # Create pre-approved sources for known podcasts
        for src in entry.get("sources", []):
            existing_source = await session.execute(
                select(Source.id).where(Source.url == src["url"])
            )
            if existing_source.scalar_one_or_none() is None:
                source = Source(
                    id=uuid.uuid4(),
                    thinker_id=actual_id,
                    source_type=src["source_type"],
                    name=src["name"],
                    url=src["url"],
                    approval_status="approved",
                    refresh_interval_hours=src.get("refresh_interval_hours"),
                    approved_backfill_days=src.get("approved_backfill_days"),
                )
                session.add(source)

        # Check if LLM approval job already exists for this thinker
        existing_job = await session.execute(
            select(Job.id).where(
                Job.job_type == "llm_approval_check",
                Job.payload["target_id"].astext == str(actual_id),
            )
        )
        if existing_job.scalar_one_or_none() is None:
            # Create LLM approval job
            job = Job(
                job_type="llm_approval_check",
                payload={
                    "review_type": "thinker_approval",
                    "target_id": str(actual_id),
                },
                priority=3,
            )
            session.add(job)

    return count


if __name__ == "__main__":

    async def _main() -> None:
        from src.thinktank.database import async_session_factory

        async with async_session_factory() as session:
            count = await seed_thinkers(session)
            await session.commit()
            print(f"Seeded {count} thinkers with LLM approval jobs")

    asyncio.run(_main())
