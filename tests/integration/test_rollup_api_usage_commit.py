"""Integration test for rollup_api_usage handler commit semantics.

HANDLERS-REVIEW CR-01: handler must commit its own writes because the
worker loop wraps the handler in `async with session_factory() as session:`
with no auto-commit. This test opens a NEW session after the handler
returns and asserts the rollup row + stale-row purge persisted.

Must fail when the handler only flushes without committing.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from tests.factories import create_job, create_rate_limit_usage
from thinktank.handlers.rollup_api_usage import handle_rollup_api_usage
from thinktank.models.api_usage import ApiUsage
from thinktank.models.rate_limit import RateLimitUsage

pytestmark = pytest.mark.anyio


def _hours_ago(n: int) -> datetime:
    """Return a timezone-aware UTC datetime n hours ago (TIMESTAMPTZ)."""
    return datetime.now(UTC) - timedelta(hours=n)


async def test_rollup_persists_across_session_boundary(session_factory):
    """Simulate worker behavior: seed in session A, run handler in session B
    (no caller commit), verify writes exist in session C.
    """
    three_hours_ago = _hours_ago(3)

    # Session A: seed raw usage rows + job, commit
    async with session_factory() as seed_session:
        for _ in range(4):
            await create_rate_limit_usage(
                seed_session, api_name="podcastindex", worker_id="w-seed", called_at=three_hours_ago
            )
        job = await create_job(seed_session, job_type="rollup_api_usage")
        await seed_session.commit()
        job_id = job.id

    # Session B: invoke handler in exactly the same way worker/loop.py does
    # (no explicit commit by caller -- handler must persist its own writes).
    async with session_factory() as handler_session:
        result = await handler_session.execute(select(type(job)).where(type(job).id == job_id))
        loaded_job = result.scalar_one()
        await handle_rollup_api_usage(handler_session, loaded_job)
        # deliberately NO session.commit() here -- worker/loop.py does not
        # call commit after `await handler(session, job)`.

    # Session C: independent verification -- rollup row must exist.
    async with session_factory() as verify_session:
        rollup_result = await verify_session.execute(select(ApiUsage).where(ApiUsage.api_name == "podcastindex"))
        rollups = rollup_result.scalars().all()
        assert len(rollups) >= 1, (
            "rollup row did not persist after handler returned; handler must commit its own transaction"
        )
        total_calls = sum(r.call_count for r in rollups)
        assert total_calls == 4

        # Old rate_limit_usage rows must have been purged as well.
        raw_result = await verify_session.execute(
            select(RateLimitUsage).where(RateLimitUsage.api_name == "podcastindex")
        )
        remaining = raw_result.scalars().all()
        assert len(remaining) == 0, "stale rate_limit_usage rows were not purged; DELETE must be committed by handler"
