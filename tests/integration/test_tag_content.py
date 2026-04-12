"""Integration tests for tag_content_thinkers handler.

Tests content attribution: source owner tagging, title/description
matching, confidence scores, skipped content handling, and duplicate
prevention.

Uses real PostgreSQL database with factory-generated test data.
"""

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.thinktank.handlers.tag_content_thinkers import handle_tag_content_thinkers
from src.thinktank.models.content import ContentThinker
from tests.factories import (
    create_content,
    create_job,
    create_source,
    create_source_thinker,
    create_thinker,
)

pytestmark = pytest.mark.anyio


async def test_source_owner_tagged_primary(session: AsyncSession):
    """Source owner is tagged as role='primary' with confidence=10."""
    owner = await create_thinker(session, name="Alice Owner")
    source = await create_source(session, thinker_id=owner.id)
    await create_source_thinker(
        session, source_id=source.id, thinker_id=owner.id, relationship_type="host"
    )
    content = await create_content(
        session,
        source_id=source.id,
        source_owner_id=owner.id,
        title="Random Episode Title",
    )
    job = await create_job(
        session,
        job_type="tag_content_thinkers",
        payload={
            "content_ids": [str(content.id)],
            "source_id": str(source.id),
            "descriptions": {str(content.id): "Some description text."},
        },
    )
    await session.commit()

    await handle_tag_content_thinkers(session, job)

    result = await session.execute(
        select(ContentThinker).where(ContentThinker.content_id == content.id)
    )
    attributions = result.scalars().all()

    # Source owner should be tagged
    owner_attrs = [a for a in attributions if a.thinker_id == owner.id]
    assert len(owner_attrs) == 1
    assert owner_attrs[0].role == "primary"
    assert owner_attrs[0].confidence == 10


async def test_title_match_tagged_guest(session: AsyncSession):
    """Thinker name in episode title -> role='guest', confidence=9."""
    owner = await create_thinker(session, name="Podcast Host")
    guest = await create_thinker(session, name="John Smith")
    source = await create_source(session, thinker_id=owner.id)
    await create_source_thinker(
        session, source_id=source.id, thinker_id=owner.id, relationship_type="host"
    )
    content = await create_content(
        session,
        source_id=source.id,
        source_owner_id=owner.id,
        title="Interview with John Smith",
    )
    job = await create_job(
        session,
        job_type="tag_content_thinkers",
        payload={
            "content_ids": [str(content.id)],
            "source_id": str(source.id),
            "descriptions": {str(content.id): "A great episode."},
        },
    )
    await session.commit()

    await handle_tag_content_thinkers(session, job)

    result = await session.execute(
        select(ContentThinker).where(
            ContentThinker.content_id == content.id,
            ContentThinker.thinker_id == guest.id,
        )
    )
    attr = result.scalar_one()
    assert attr.role == "guest"
    assert attr.confidence == 9


async def test_description_match_tagged_guest(session: AsyncSession):
    """Thinker name in description (via payload) -> role='guest', confidence=6."""
    owner = await create_thinker(session, name="Podcast Host")
    guest = await create_thinker(session, name="Jane Doe")
    source = await create_source(session, thinker_id=owner.id)
    await create_source_thinker(
        session, source_id=source.id, thinker_id=owner.id, relationship_type="host"
    )
    content = await create_content(
        session,
        source_id=source.id,
        source_owner_id=owner.id,
        title="General Episode Title",
    )
    job = await create_job(
        session,
        job_type="tag_content_thinkers",
        payload={
            "content_ids": [str(content.id)],
            "source_id": str(source.id),
            "descriptions": {
                str(content.id): "In this episode we talk with Jane Doe about AI."
            },
        },
    )
    await session.commit()

    await handle_tag_content_thinkers(session, job)

    result = await session.execute(
        select(ContentThinker).where(
            ContentThinker.content_id == content.id,
            ContentThinker.thinker_id == guest.id,
        )
    )
    attr = result.scalar_one()
    assert attr.role == "guest"
    assert attr.confidence == 6


async def test_no_match_only_primary(session: AsyncSession):
    """Content with no thinker names in title/desc -> only source owner attribution."""
    owner = await create_thinker(session, name="Solo Host")
    other = await create_thinker(session, name="Unrelated Thinker")
    source = await create_source(session, thinker_id=owner.id)
    await create_source_thinker(
        session, source_id=source.id, thinker_id=owner.id, relationship_type="host"
    )
    content = await create_content(
        session,
        source_id=source.id,
        source_owner_id=owner.id,
        title="Weekly Roundup",
    )
    job = await create_job(
        session,
        job_type="tag_content_thinkers",
        payload={
            "content_ids": [str(content.id)],
            "source_id": str(source.id),
            "descriptions": {str(content.id): "A regular weekly episode."},
        },
    )
    await session.commit()

    await handle_tag_content_thinkers(session, job)

    result = await session.execute(
        select(ContentThinker).where(ContentThinker.content_id == content.id)
    )
    attributions = result.scalars().all()

    # Only the source owner should be attributed
    assert len(attributions) == 1
    assert attributions[0].thinker_id == owner.id
    assert attributions[0].role == "primary"


async def test_multiple_thinkers_matched(session: AsyncSession):
    """Title mentions two existing thinkers -> two guest attributions + one primary."""
    owner = await create_thinker(session, name="Show Host")
    guest1 = await create_thinker(session, name="Alice Walker")
    guest2 = await create_thinker(session, name="Bob Martin")
    source = await create_source(session, thinker_id=owner.id)
    await create_source_thinker(
        session, source_id=source.id, thinker_id=owner.id, relationship_type="host"
    )
    content = await create_content(
        session,
        source_id=source.id,
        source_owner_id=owner.id,
        title="Debate: Alice Walker vs Bob Martin",
    )
    job = await create_job(
        session,
        job_type="tag_content_thinkers",
        payload={
            "content_ids": [str(content.id)],
            "source_id": str(source.id),
            "descriptions": {str(content.id): "A heated debate."},
        },
    )
    await session.commit()

    await handle_tag_content_thinkers(session, job)

    result = await session.execute(
        select(ContentThinker).where(ContentThinker.content_id == content.id)
    )
    attributions = result.scalars().all()

    assert len(attributions) == 3  # owner (primary) + 2 guests

    by_thinker = {a.thinker_id: a for a in attributions}
    assert by_thinker[owner.id].role == "primary"
    assert by_thinker[owner.id].confidence == 10
    assert by_thinker[guest1.id].role == "guest"
    assert by_thinker[guest1.id].confidence == 9
    assert by_thinker[guest2.id].role == "guest"
    assert by_thinker[guest2.id].confidence == 9


async def test_skipped_content_not_attributed(session: AsyncSession):
    """Content with status='skipped' -> no ContentThinker rows created."""
    owner = await create_thinker(session, name="Host Name")
    source = await create_source(session, thinker_id=owner.id)
    await create_source_thinker(
        session, source_id=source.id, thinker_id=owner.id, relationship_type="host"
    )
    content = await create_content(
        session,
        source_id=source.id,
        source_owner_id=owner.id,
        title="Skipped Episode with Host Name",
        status="skipped",
    )
    job = await create_job(
        session,
        job_type="tag_content_thinkers",
        payload={
            "content_ids": [str(content.id)],
            "source_id": str(source.id),
            "descriptions": {str(content.id): "This should be skipped."},
        },
    )
    await session.commit()

    await handle_tag_content_thinkers(session, job)

    result = await session.execute(
        select(ContentThinker).where(ContentThinker.content_id == content.id)
    )
    assert len(result.scalars().all()) == 0


async def test_duplicate_attribution_prevented(session: AsyncSession):
    """Running handler twice on same content -> no duplicate ContentThinker rows."""
    owner = await create_thinker(session, name="Repeat Host")
    guest = await create_thinker(session, name="Repeat Guest")
    source = await create_source(session, thinker_id=owner.id)
    await create_source_thinker(
        session, source_id=source.id, thinker_id=owner.id, relationship_type="host"
    )
    content = await create_content(
        session,
        source_id=source.id,
        source_owner_id=owner.id,
        title="Episode with Repeat Guest",
    )

    payload = {
        "content_ids": [str(content.id)],
        "source_id": str(source.id),
        "descriptions": {str(content.id): "A repeat episode."},
    }

    job1 = await create_job(
        session,
        job_type="tag_content_thinkers",
        payload=payload,
    )
    await session.commit()

    # First run
    await handle_tag_content_thinkers(session, job1)

    result = await session.execute(
        select(ContentThinker).where(ContentThinker.content_id == content.id)
    )
    first_count = len(result.scalars().all())
    assert first_count == 2  # owner + guest

    # Second run with new job
    job2 = await create_job(
        session,
        job_type="tag_content_thinkers",
        payload=payload,
    )
    await session.commit()

    await handle_tag_content_thinkers(session, job2)

    result = await session.execute(
        select(ContentThinker).where(ContentThinker.content_id == content.id)
    )
    second_count = len(result.scalars().all())
    assert second_count == 2  # No duplicates
