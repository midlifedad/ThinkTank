"""Seed initial sources — top podcasts and content channels.

Sources are first-class entities independent of thinkers. Each source is created
with approval_status="pending_llm" and an llm_approval_check job for LLM review.

Host names are stored as metadata on the source; hosts are NOT necessarily thinkers.
The many-to-many relationship between sources and thinkers is managed via the
source_thinkers junction table.

Uses ON CONFLICT DO NOTHING for idempotent source inserts (unique on URL).

Usage:
    python -m scripts.seed_sources
"""

import asyncio
import re
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from thinktank.models.job import Job
from thinktank.models.source import Source

INITIAL_SOURCES = [
    # Tier 1 — Top-tier podcasts for AI/tech guest appearances
    {
        "name": "Lex Fridman Podcast",
        "slug": "lex-fridman-podcast",
        "url": "https://lexfridman.com/feed/podcast/",
        "source_type": "podcast_rss",
        "tier": 1,
        "host_name": "Lex Fridman",
        "description": "Long-form interviews with leaders in AI, science, and technology",
        "refresh_interval_hours": 6,
        "approved_backfill_days": 365,
    },
    {
        "name": "All-In Podcast",
        "slug": "all-in-podcast",
        "url": "https://feeds.megaphone.fm/all-in-with-chamath-jason-sacks-and-friedberg",
        "source_type": "podcast_rss",
        "tier": 1,
        "host_name": "Chamath Palihapitiya, Jason Calacanis, David Sacks, David Friedberg",
        "description": "Technology, economics, and startup analysis from Silicon Valley insiders",
        "refresh_interval_hours": 6,
        "approved_backfill_days": 365,
    },
    {
        "name": "Joe Rogan Experience",
        "slug": "joe-rogan-experience",
        "url": "https://feeds.megaphone.fm/GLT1412515089",
        "source_type": "podcast_rss",
        "tier": 1,
        "host_name": "Joe Rogan",
        "description": "Broad guest range including many tech and science thought leaders",
        "refresh_interval_hours": 6,
        "approved_backfill_days": 365,
    },
    {
        "name": "Huberman Lab",
        "slug": "huberman-lab",
        "url": "https://feeds.megaphone.fm/hubermanlab",
        "source_type": "podcast_rss",
        "tier": 1,
        "host_name": "Andrew Huberman",
        "description": "Science-based podcast on neuroscience, health, and performance",
        "refresh_interval_hours": 6,
        "approved_backfill_days": 365,
    },
    {
        "name": "Acquired",
        "slug": "acquired",
        "url": "https://feeds.megaphone.fm/acquired",
        "source_type": "podcast_rss",
        "tier": 1,
        "host_name": "Ben Gilbert, David Rosenthal",
        "description": "Deep dives into technology companies and their stories",
        "refresh_interval_hours": 12,
        "approved_backfill_days": 365,
    },
    # Tier 2 — Strong podcasts with relevant guests
    {
        "name": "Dwarkesh Podcast",
        "slug": "dwarkesh-podcast",
        "url": "https://api.substack.com/feed/podcast/1084089/s/80641.rss",
        "source_type": "podcast_rss",
        "tier": 2,
        "host_name": "Dwarkesh Patel",
        "description": "Deep technical interviews with AI researchers and technologists",
        "refresh_interval_hours": 24,
        "approved_backfill_days": 365,
    },
    {
        "name": "Conversations with Tyler",
        "slug": "conversations-with-tyler",
        "url": "https://feeds.megaphone.fm/conversations-with-tyler",
        "source_type": "podcast_rss",
        "tier": 2,
        "host_name": "Tyler Cowen",
        "description": "Economics, culture, and ideas with leading thinkers",
        "refresh_interval_hours": 24,
        "approved_backfill_days": 180,
    },
    {
        "name": "No Priors",
        "slug": "no-priors",
        "url": "https://anchor.fm/s/de5b2264/podcast/rss",
        "source_type": "podcast_rss",
        "tier": 2,
        "host_name": "Sarah Guo, Elad Gil",
        "description": "AI-focused interviews with founders and researchers",
        "refresh_interval_hours": 24,
        "approved_backfill_days": 180,
    },
    {
        "name": "Tim Ferriss Show",
        "slug": "tim-ferriss-show",
        "url": "https://rss.art19.com/tim-ferriss-show",
        "source_type": "podcast_rss",
        "tier": 2,
        "host_name": "Tim Ferriss",
        "description": "World-class performers across tech, business, and science",
        "refresh_interval_hours": 24,
        "approved_backfill_days": 180,
    },
    {
        "name": "80,000 Hours",
        "slug": "80000-hours",
        "url": "https://feeds.80000hours.org/podcast.rss",
        "source_type": "podcast_rss",
        "tier": 2,
        "host_name": "Rob Wiblin",
        "description": "AI safety, philosophy, and effective altruism perspectives",
        "refresh_interval_hours": 24,
        "approved_backfill_days": 180,
    },
]


async def seed_sources(session: AsyncSession) -> int:
    """Seed initial sources with LLM approval jobs.

    For each source:
    1. Check if source already exists (by URL)
    2. Create source if new
    3. Create LLM approval job if none exists

    Returns the number of sources seeded.
    """
    count = 0

    for entry in INITIAL_SOURCES:
        # Check for existing source by URL
        existing = await session.execute(
            select(Source.id).where(Source.url == entry["url"])
        )
        if existing.scalar_one_or_none() is not None:
            count += 1
            continue

        source = Source(
            id=uuid.uuid4(),
            thinker_id=None,
            source_type=entry["source_type"],
            name=entry["name"],
            slug=entry["slug"],
            url=entry["url"],
            tier=entry.get("tier", 2),
            host_name=entry.get("host_name"),
            description=entry.get("description"),
            approval_status="pending_llm",
            refresh_interval_hours=entry.get("refresh_interval_hours"),
            approved_backfill_days=entry.get("approved_backfill_days"),
        )
        session.add(source)
        count += 1

        # Create LLM approval job
        existing_job = await session.execute(
            select(Job.id).where(
                Job.job_type == "llm_approval_check",
                Job.payload["target_id"].astext == str(source.id),
            )
        )
        if existing_job.scalar_one_or_none() is None:
            job = Job(
                job_type="llm_approval_check",
                payload={
                    "review_type": "source_approval",
                    "target_id": str(source.id),
                },
                priority=3,
            )
            session.add(job)

    return count


if __name__ == "__main__":

    async def _main() -> None:
        from thinktank.database import async_session_factory

        async with async_session_factory() as session:
            count = await seed_sources(session)
            await session.commit()
            print(f"Seeded {count} sources with LLM approval jobs")

    asyncio.run(_main())
