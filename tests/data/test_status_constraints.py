"""Integration tests for CHECK constraints on status columns.

Source: DATA-REVIEW H3 -- status columns used to accept any TEXT value,
so typos silently produced rows with unrecognised statuses. Migration
006 adds a CHECK constraint whose allowed-value list comes from
`thinktank.models.constants`; these tests pin down that:

1. Every documented allowed value inserts successfully.
2. An unknown value raises IntegrityError.
"""

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tests.factories import create_content, create_job, create_source, create_thinker
from thinktank.models.constants import ALLOWED_CONTENT_STATUSES, ALLOWED_JOB_STATUSES, ALLOWED_SOURCE_APPROVAL_STATUSES

# ---------- Content.status ----------


@pytest.mark.asyncio
async def test_invalid_content_status_raises(session: AsyncSession):
    """Content.status outside ALLOWED_CONTENT_STATUSES is rejected."""
    await create_thinker(session)
    source = await create_source(session)
    await session.commit()

    with pytest.raises(IntegrityError):
        await create_content(session, source_id=source.id, status="not_a_real_status")
        await session.commit()
    await session.rollback()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ALLOWED_CONTENT_STATUSES)
async def test_valid_content_status_accepted(session: AsyncSession, status: str):
    """Every value in ALLOWED_CONTENT_STATUSES inserts successfully."""
    await create_thinker(session)
    source = await create_source(session)
    content = await create_content(session, source_id=source.id, status=status)
    await session.commit()
    assert content.status == status


# ---------- Source.approval_status ----------


@pytest.mark.asyncio
async def test_invalid_source_approval_status_raises(session: AsyncSession):
    """Source.approval_status outside the allowed set is rejected."""
    with pytest.raises(IntegrityError):
        await create_source(session, approval_status="definitely_not_valid")
        await session.commit()
    await session.rollback()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ALLOWED_SOURCE_APPROVAL_STATUSES)
async def test_valid_source_approval_status_accepted(session: AsyncSession, status: str):
    """Every value in ALLOWED_SOURCE_APPROVAL_STATUSES inserts successfully."""
    source = await create_source(session, approval_status=status)
    await session.commit()
    assert source.approval_status == status


# ---------- Job.status ----------


@pytest.mark.asyncio
async def test_invalid_job_status_raises(session: AsyncSession):
    """Job.status outside ALLOWED_JOB_STATUSES is rejected."""
    with pytest.raises(IntegrityError):
        await create_job(session, status="bogus_job_state")
        await session.commit()
    await session.rollback()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ALLOWED_JOB_STATUSES)
async def test_valid_job_status_accepted(session: AsyncSession, status: str):
    """Every value in ALLOWED_JOB_STATUSES inserts successfully."""
    job = await create_job(session, status=status)
    await session.commit()
    assert job.status == status
