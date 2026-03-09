"""Seed initial thinkers with LLM approval jobs.

Each thinker is created with approval_status="pending_llm" and a corresponding
llm_approval_check job is enqueued for LLM review. Uses ON CONFLICT DO UPDATE
for idempotent thinker upserts and checks for existing jobs to avoid duplicates.

Usage:
    python -m scripts.seed_thinkers
"""

import asyncio
import uuid

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.thinktank.models.job import Job
from src.thinktank.models.thinker import Thinker

INITIAL_THINKERS = [
    {
        "name": "Lex Fridman",
        "slug": "lex-fridman",
        "tier": 1,
        "bio": "MIT AI researcher and podcast host",
    },
    {
        "name": "Andrew Huberman",
        "slug": "andrew-huberman",
        "tier": 1,
        "bio": "Stanford neuroscientist",
    },
    {
        "name": "Balaji Srinivasan",
        "slug": "balaji-srinivasan",
        "tier": 2,
        "bio": "Technology entrepreneur and author",
    },
    {
        "name": "Tyler Cowen",
        "slug": "tyler-cowen",
        "tier": 2,
        "bio": "Economist and author of Marginal Revolution",
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
    2. Check if an llm_approval_check job already exists for this thinker
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
            set_={"name": entry["name"], "tier": entry["tier"], "bio": entry["bio"]},
        ).returning(Thinker.id)

        result = await session.execute(stmt)
        actual_id = result.scalar_one()
        count += 1

        # Check if LLM approval job already exists for this thinker
        existing_job = await session.execute(
            select(Job.id).where(
                Job.job_type == "llm_approval_check",
                Job.payload["thinker_id"].astext == str(actual_id),
            )
        )
        if existing_job.scalar_one_or_none() is None:
            # Create LLM approval job
            job = Job(
                job_type="llm_approval_check",
                payload={
                    "review_type": "thinker_approval",
                    "thinker_id": str(actual_id),
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
