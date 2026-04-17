"""Integration tests for discover_thinker fan-out handler.

Covers eligibility gating, PodcastIndex credential check, owned-source fan-out,
and dedup against in-flight jobs (HANDLERS-REVIEW ME-07).
"""

from unittest.mock import patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.factories import create_job, create_thinker
from thinktank.handlers.discover_thinker import handle_discover_thinker
from thinktank.models.job import Job

pytestmark = pytest.mark.anyio


async def test_skips_guest_discovery_when_podcastindex_unavailable(
    session: AsyncSession,
):
    """No PodcastIndex creds in DB or env -> no discover_guests job enqueued (ME-07).

    Previously the handler always enqueued discover_guests_podcastindex even with
    no credentials, forcing the downstream handler to fail every run and burning
    queue slots + backoff bookkeeping.
    """
    thinker = await create_thinker(session, approval_status="approved", active=True)
    trigger_job = await create_job(
        session,
        job_type="discover_thinker",
        payload={"thinker_id": str(thinker.id)},
    )
    await session.commit()

    with patch.dict("os.environ", {}, clear=False):
        # Scrub both vars to force the disabled path
        import os

        os.environ.pop("PODCASTINDEX_API_KEY", None)
        os.environ.pop("PODCASTINDEX_API_SECRET", None)
        await handle_discover_thinker(session, trigger_job)

    result = await session.execute(select(Job).where(Job.job_type == "discover_guests_podcastindex"))
    assert result.scalars().all() == []


async def test_enqueues_guest_discovery_when_podcastindex_configured(
    session: AsyncSession,
):
    """With PodcastIndex API key present -> discover_guests job enqueued."""
    thinker = await create_thinker(session, approval_status="approved", active=True)
    trigger_job = await create_job(
        session,
        job_type="discover_thinker",
        payload={"thinker_id": str(thinker.id)},
    )
    await session.commit()

    with patch.dict(
        "os.environ",
        {"PODCASTINDEX_API_KEY": "test-key", "PODCASTINDEX_API_SECRET": "test-secret"},
    ):
        await handle_discover_thinker(session, trigger_job)

    result = await session.execute(
        select(Job).where(
            Job.job_type == "discover_guests_podcastindex",
            Job.payload["thinker_id"].astext == str(thinker.id),
        )
    )
    jobs = result.scalars().all()
    assert len(jobs) == 1


async def test_skips_when_thinker_not_approved(session: AsyncSession):
    """Thinker in pending status -> no jobs enqueued regardless of creds."""
    thinker = await create_thinker(session, approval_status="pending_llm", active=True)
    trigger_job = await create_job(
        session,
        job_type="discover_thinker",
        payload={"thinker_id": str(thinker.id)},
    )
    await session.commit()

    with patch.dict(
        "os.environ",
        {"PODCASTINDEX_API_KEY": "test-key", "PODCASTINDEX_API_SECRET": "test-secret"},
    ):
        await handle_discover_thinker(session, trigger_job)

    result = await session.execute(select(Job).where(Job.job_type == "discover_guests_podcastindex"))
    assert result.scalars().all() == []
