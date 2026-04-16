"""Integration tests for timezone-aware timestamp storage.

Source: DATA-REVIEW H4, INTEGRATIONS-REVIEW H-03, HANDLERS-REVIEW LO-06.

After migration 007, every timestamp column is TIMESTAMPTZ. Stored values
must round-trip as timezone-aware datetimes with UTC tzinfo regardless of
whether the caller inserted naive-UTC or aware.
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tests.factories import (
    create_content,
    create_job,
    create_source,
    create_thinker,
)


@pytest.mark.asyncio
async def test_thinker_added_at_roundtrips_aware_utc(session: AsyncSession):
    """Thinker.added_at round-trips as timezone-aware UTC datetime."""
    thinker = await create_thinker(session)
    await session.commit()
    await session.refresh(thinker)

    assert thinker.added_at is not None
    assert thinker.added_at.tzinfo is not None, (
        "Expected timezone-aware datetime after migration 007"
    )
    # UTC offset must be zero.
    assert thinker.added_at.utcoffset().total_seconds() == 0


@pytest.mark.asyncio
async def test_source_created_at_roundtrips_aware_utc(session: AsyncSession):
    """Source.created_at round-trips as timezone-aware UTC."""
    source = await create_source(session)
    await session.commit()
    await session.refresh(source)

    assert source.created_at.tzinfo is not None
    assert source.created_at.utcoffset().total_seconds() == 0


@pytest.mark.asyncio
async def test_content_discovered_at_roundtrips_aware_utc(session: AsyncSession):
    """Content.discovered_at round-trips as timezone-aware UTC."""
    source = await create_source(session)
    await session.commit()
    content = await create_content(session, source_id=source.id)
    await session.commit()
    await session.refresh(content)

    assert content.discovered_at.tzinfo is not None
    assert content.discovered_at.utcoffset().total_seconds() == 0


@pytest.mark.asyncio
async def test_job_created_at_roundtrips_aware_utc(session: AsyncSession):
    """Job.created_at round-trips as timezone-aware UTC."""
    job = await create_job(session)
    await session.commit()
    await session.refresh(job)

    assert job.created_at.tzinfo is not None
    assert job.created_at.utcoffset().total_seconds() == 0


@pytest.mark.asyncio
async def test_aware_datetime_insert_roundtrips_unchanged(session: AsyncSession):
    """An aware UTC datetime inserted explicitly round-trips as the same instant."""
    source = await create_source(session)
    await session.commit()
    published = datetime(2026, 4, 1, 12, 30, 0, tzinfo=UTC)
    content = await create_content(
        session, source_id=source.id, published_at=published
    )
    await session.commit()
    await session.refresh(content)

    assert content.published_at == published
    assert content.published_at.tzinfo is not None
