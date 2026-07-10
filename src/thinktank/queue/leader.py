"""Advisory-lock leader election for singleton scheduler ticks.

Source: ARCH-REVIEW 2026-05-28 (A4). Every worker process starts the full
set of background schedulers (GPU scaling, LLM governance crons, the A1
recurring-task executor). At one replica that's fine; at N replicas you
get N daily digests, N weekly audits, and N competing autoscale decisions.

Each scheduler tick now runs under a Postgres transaction-scoped advisory
lock: the first replica to reach the tick does the work, the others skip
that tick entirely (``pg_try_advisory_xact_lock`` never blocks). The lock
releases automatically at commit/rollback, so a crashed holder can never
wedge the schedulers.

Lock key space: this module owns small integers 100-199. Key 1 is used by
``alembic/env.py`` for migration serialization; the rate limiter derives
its keys from a hash of the api_name (see ``stable_lock_key``).
"""

from __future__ import annotations

import hashlib

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Scheduler singleton lock keys (owned range: 100-199).
LOCK_GPU_SCALING = 101
LOCK_LLM_ESCALATION = 102
LOCK_LLM_HEALTH_CHECK = 103
LOCK_LLM_DAILY_DIGEST = 104
LOCK_LLM_WEEKLY_AUDIT = 105
LOCK_RECURRING_TASKS = 106


def stable_lock_key(name: str) -> int:
    """Derive a stable positive int lock key from a string.

    Python's builtin ``hash()`` is randomized per process (PYTHONHASHSEED),
    so two worker containers derive DIFFERENT advisory-lock keys for the
    same name and never actually contend -- silently defeating any
    cross-process serialization built on it (A4; the rate limiter had
    exactly this bug). SHA-256 is stable across processes and machines.
    """
    digest = hashlib.sha256(name.encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big") & 0x7FFFFFFF


async def try_advisory_xact_lock(session: AsyncSession, key: int) -> bool:
    """Attempt a transaction-scoped advisory lock without blocking.

    Returns True if this session now holds the lock (released at
    commit/rollback), False if another session holds it -- in which case
    the caller should skip its tick, not wait.
    """
    result = await session.execute(text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": key})
    return bool(result.scalar_one())
