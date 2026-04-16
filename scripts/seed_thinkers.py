"""Seed initial thinkers — top minds in AI, science, and technology.

Each thinker is created with approval_status="pending_llm" and a corresponding
llm_approval_check job is enqueued for LLM review.

NOTE: Thinkers are people whose IDEAS and OPINIONS we want to track (e.g., Jensen
Huang, Demis Hassabis). Podcast hosts who are primarily interviewers (e.g., Lex
Fridman) are seeded as sources, not thinkers.

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

from thinktank.models.job import Job
from thinktank.models.thinker import Thinker

INITIAL_THINKERS = [
    # Tier 1 — Most influential voices in AI/tech
    {
        "name": "Jensen Huang",
        "slug": "jensen-huang",
        "tier": 1,
        "bio": "NVIDIA CEO, driving the AI hardware revolution",
    },
    {
        "name": "Demis Hassabis",
        "slug": "demis-hassabis",
        "tier": 1,
        "bio": "DeepMind CEO, Nobel laureate, AGI research pioneer",
    },
    {
        "name": "Sam Altman",
        "slug": "sam-altman",
        "tier": 1,
        "bio": "OpenAI CEO, leading commercial AGI development",
    },
    {
        "name": "Dario Amodei",
        "slug": "dario-amodei",
        "tier": 1,
        "bio": "Anthropic CEO, AI safety researcher",
    },
    {
        "name": "Ilya Sutskever",
        "slug": "ilya-sutskever",
        "tier": 1,
        "bio": "SSI co-founder, former OpenAI Chief Scientist",
    },
    {
        "name": "Yann LeCun",
        "slug": "yann-lecun",
        "tier": 1,
        "bio": "Meta Chief AI Scientist, Turing Award winner",
    },
    {
        "name": "Geoffrey Hinton",
        "slug": "geoffrey-hinton",
        "tier": 1,
        "bio": "Godfather of deep learning, Nobel laureate, AI safety advocate",
    },
    # Tier 2 — Highly influential thinkers
    {
        "name": "Andrej Karpathy",
        "slug": "andrej-karpathy",
        "tier": 2,
        "bio": "Former Tesla/OpenAI, AI educator and researcher",
    },
    {
        "name": "Fei-Fei Li",
        "slug": "fei-fei-li",
        "tier": 2,
        "bio": "Stanford professor, ImageNet pioneer, World Labs founder",
    },
    {
        "name": "Satya Nadella",
        "slug": "satya-nadella",
        "tier": 2,
        "bio": "Microsoft CEO, driving enterprise AI adoption",
    },
    {
        "name": "Balaji Srinivasan",
        "slug": "balaji-srinivasan",
        "tier": 2,
        "bio": "Technology entrepreneur, author of The Network State",
    },
    {
        "name": "Marc Andreessen",
        "slug": "marc-andreessen",
        "tier": 2,
        "bio": "a16z co-founder, technology optimist and philosopher",
    },
    {
        "name": "Ray Kurzweil",
        "slug": "ray-kurzweil",
        "tier": 2,
        "bio": "Futurist, inventor, author of The Singularity Is Nearer",
    },
    {
        "name": "Peter Diamandis",
        "slug": "peter-diamandis",
        "tier": 2,
        "bio": "XPRIZE founder, abundance thinker, longevity advocate",
    },
    {
        "name": "Joscha Bach",
        "slug": "joscha-bach",
        "tier": 2,
        "bio": "AI researcher and cognitive scientist",
    },
]


async def seed_thinkers(session: AsyncSession) -> int:
    """Seed initial thinkers with LLM approval jobs.

    For each thinker:
    1. Upsert the thinker row (ON CONFLICT DO UPDATE on slug)
    2. Check if an llm_approval_check job already exists
    3. If not, create one to trigger LLM review

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
        ).on_conflict_do_update(
            index_elements=["slug"],
            set_={
                "name": entry["name"],
                "tier": entry["tier"],
                "bio": entry["bio"],
            },
        ).returning(Thinker.id)

        result = await session.execute(stmt)
        actual_id = result.scalar_one()
        count += 1

        # Check if LLM approval job already exists for this thinker
        existing_job = await session.execute(
            select(Job.id).where(
                Job.job_type == "llm_approval_check",
                Job.payload["target_id"].astext == str(actual_id),
            )
        )
        if existing_job.scalar_one_or_none() is None:
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
        from thinktank.database import async_session_factory

        async with async_session_factory() as session:
            count = await seed_thinkers(session)
            await session.commit()
            print(f"Seeded {count} thinkers with LLM approval jobs")

    asyncio.run(_main())
