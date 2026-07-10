"""Contract tests for advisory-lock leader election (A4).

Source: ARCH-REVIEW 2026-05-28. Worker cron schedulers (GPU scaling, LLM
governance, recurring tasks) run in every replica; the advisory lock makes
each tick a singleton across the fleet.
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from thinktank.queue.leader import (
    LOCK_LLM_DAILY_DIGEST,
    stable_lock_key,
    try_advisory_xact_lock,
)

pytestmark = pytest.mark.anyio


class TestStableLockKey:
    def test_deterministic_across_calls(self):
        """Same name always yields the same key (unlike builtin hash())."""
        assert stable_lock_key("youtube") == stable_lock_key("youtube")

    def test_known_value_pinned(self):
        """Pin the derivation so a refactor can't silently change keys
        (which would momentarily disable cross-process contention)."""
        import hashlib

        expected = int.from_bytes(hashlib.sha256(b"youtube").digest()[:4], "big") & 0x7FFFFFFF
        assert stable_lock_key("youtube") == expected

    def test_positive_int31(self):
        for name in ("youtube", "podcastindex", "anthropic", ""):
            key = stable_lock_key(name)
            assert 0 <= key <= 0x7FFFFFFF

    def test_distinct_names_distinct_keys(self):
        assert stable_lock_key("youtube") != stable_lock_key("podcastindex")


class TestTryAdvisoryXactLock:
    async def test_acquires_when_free(self, session: AsyncSession):
        assert await try_advisory_xact_lock(session, LOCK_LLM_DAILY_DIGEST) is True

    async def test_second_session_skips_while_held(self, session: AsyncSession, session_factory):
        """A second session must get False (skip its tick) while the first
        transaction holds the lock."""
        assert await try_advisory_xact_lock(session, LOCK_LLM_DAILY_DIGEST) is True

        async with session_factory() as other:
            assert await try_advisory_xact_lock(other, LOCK_LLM_DAILY_DIGEST) is False

    async def test_released_after_commit(self, session: AsyncSession, session_factory):
        """xact scope: commit releases the lock for the next tick."""
        assert await try_advisory_xact_lock(session, LOCK_LLM_DAILY_DIGEST) is True
        await session.commit()

        async with session_factory() as other:
            assert await try_advisory_xact_lock(other, LOCK_LLM_DAILY_DIGEST) is True
            await other.commit()
