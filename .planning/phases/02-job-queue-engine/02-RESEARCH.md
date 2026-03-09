# Phase 2: Job Queue Engine - Research

**Researched:** 2026-03-08
**Domain:** PostgreSQL-backed job queue with priority, retry, rate limiting, backpressure, and kill switch
**Confidence:** HIGH

## Summary

Phase 2 builds the job queue engine that drives all subsequent work in ThinkTank. The core pattern is `SELECT FOR UPDATE SKIP LOCKED` on PostgreSQL -- a proven approach used in production by Solid Queue (Ruby/Rails), PGQueuer, Procrastinate, and Graphile Worker. The pattern is well-documented and eliminates the need for Redis or any external message broker. SQLAlchemy 2.0 supports this natively via `select().with_for_update(skip_locked=True)`.

The phase delivers nine capabilities: (1) atomic job claiming with priority ordering, (2) an async worker loop that polls and dispatches jobs by type, (3) exponential backoff retry with per-type max attempts, (4) stale job reclamation for stuck `running` jobs, (5) sliding-window rate limit coordination via the `rate_limit_usage` table, (6) backpressure that demotes discovery priority when transcription queue is deep, (7) a global kill switch via `workers_active` in `system_config`, (8) a closed set of error categories on failed jobs, and (9) contract tests for every job handler. All coordination happens through PostgreSQL -- no in-memory state, no external dependencies.

The existing Phase 1 infrastructure provides a strong foundation: the `Job` model already has the `ix_jobs_claim` index on `(status, priority, scheduled_at)`, the `RateLimitUsage` model has the `ix_rate_limit_usage_window` index on `(api_name, called_at)`, and `SystemConfig` is ready for operational parameters. The async session factory, structlog logging, and test fixtures are all in place. Phase 2 builds pure queue infrastructure on top of these -- no domain-specific job handlers (those come in Phase 3+).

**Primary recommendation:** Implement a two-transaction claim pattern -- one short transaction to SELECT FOR UPDATE SKIP LOCKED + UPDATE status to 'running', then a separate transaction for job execution. Never hold row locks during processing. The worker loop should be an asyncio task that polls every 2 seconds when active, backing off to 30 seconds when idle, and checking `workers_active` on every poll cycle.

## Standard Stack

### Core (Already Installed)

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| SQLAlchemy | >=2.0.46 | ORM + `with_for_update(skip_locked=True)` | Native SKIP LOCKED support, async sessions, 2.0-style select() |
| asyncpg | latest | PostgreSQL async driver | Fastest async PG driver, already configured in database.py |
| structlog | >=25.5.0 | Structured logging for worker lifecycle | Already configured with correlation IDs, JSON output |
| pydantic | >=2.12.5 | Job payload validation, config models | Already used for Settings, natural for typed job payloads |
| pytest + pytest-asyncio | >=8.0 / >=0.25.0 | Testing async worker code | Already configured with session-scoped event loop |

### No New Dependencies Required

Phase 2 requires zero new library installations. Everything needed is already in `pyproject.toml`. The job queue, worker loop, rate limiter, backpressure, and kill switch are all pure application code built on SQLAlchemy + asyncio + PostgreSQL.

## Architecture Patterns

### Recommended Project Structure

```
src/thinktank/
├── worker/
│   ├── __init__.py          # Already exists (empty)
│   ├── loop.py              # Worker main loop (poll, claim, dispatch)
│   ├── config.py            # Worker-specific settings (poll intervals, concurrency)
│   └── __main__.py          # Entry point: python -m thinktank.worker
├── queue/
│   ├── __init__.py
│   ├── claim.py             # claim_job(), complete_job(), fail_job()
│   ├── retry.py             # Exponential backoff, should_retry()
│   ├── reclaim.py           # reclaim_stale_jobs() scheduled task
│   ├── rate_limiter.py      # check_rate_limit(), record_api_call()
│   ├── backpressure.py      # check_backpressure(), get_effective_priority()
│   ├── kill_switch.py       # is_workers_active()
│   └── errors.py            # ErrorCategory enum, categorize_error()
├── handlers/
│   ├── __init__.py
│   ├── registry.py          # JOB_HANDLERS: dict[str, Callable] dispatch map
│   └── base.py              # JobHandler protocol / base interface
tests/
├── unit/
│   ├── test_retry.py        # Pure backoff math
│   ├── test_backpressure.py # Priority demotion logic
│   ├── test_errors.py       # Error categorization
│   └── test_rate_limiter.py # Sliding window calculation
├── integration/
│   ├── test_claim.py        # SELECT FOR UPDATE SKIP LOCKED against real PG
│   ├── test_reclaim.py      # Stale job reclamation with real timestamps
│   ├── test_kill_switch.py  # workers_active flag behavior
│   ├── test_rate_limit.py   # rate_limit_usage table coordination
│   ├── test_backpressure.py # Queue depth threshold checks
│   └── test_worker_loop.py  # Full loop lifecycle (start, claim, dispatch, stop)
├── contract/
│   └── test_handler_contracts.py  # Contract tests for handler interface
```

### Pattern 1: Two-Transaction Job Claim

**What:** Separate the job lock acquisition from job processing into two distinct database transactions.

**When to use:** Always. This is the fundamental pattern for the entire queue.

**Why:** Holding a row lock during processing (which may take seconds to minutes) blocks other workers from even seeing the row. The `SKIP LOCKED` pattern skips locked rows, but holding locks during processing wastes the lock window. The claim transaction should be <10ms.

**Example:**

```python
# Source: PostgreSQL SKIP LOCKED pattern + SQLAlchemy 2.0 docs
from sqlalchemy import select, update, text
from sqlalchemy.ext.asyncio import AsyncSession

async def claim_job(
    session: AsyncSession,
    worker_id: str,
    job_types: list[str] | None = None,
) -> Job | None:
    """Claim the highest-priority eligible job atomically.

    Uses SELECT FOR UPDATE SKIP LOCKED to prevent two workers
    from ever claiming the same job. Returns None if no work available.
    """
    stmt = (
        select(Job)
        .where(
            Job.status.in_(["pending", "retrying"]),
            Job.scheduled_at <= text("NOW()"),
        )
        .order_by(Job.priority.asc(), Job.scheduled_at.asc())
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if job_types:
        stmt = stmt.where(Job.job_type.in_(job_types))

    result = await session.execute(stmt)
    job = result.scalar_one_or_none()

    if job is None:
        return None

    # Update to running in the same transaction
    job.status = "running"
    job.worker_id = worker_id
    job.started_at = _now()
    job.attempts += 1
    await session.commit()  # Release the FOR UPDATE lock immediately

    return job
```

### Pattern 2: Async Worker Loop with Graceful Shutdown

**What:** An asyncio task that continuously polls for jobs, dispatches them, and handles shutdown signals.

**When to use:** The main entry point for both CPU and GPU worker services.

**Example:**

```python
import asyncio
import signal

async def worker_loop(
    session_factory: async_sessionmaker,
    worker_id: str,
    job_types: list[str] | None = None,
    max_concurrency: int = 4,
) -> None:
    """Main worker loop. Polls for jobs, dispatches handlers."""
    shutdown_event = asyncio.Event()
    semaphore = asyncio.Semaphore(max_concurrency)
    active_tasks: set[asyncio.Task] = set()

    # Register signal handlers for graceful shutdown
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, shutdown_event.set)

    poll_interval = 2.0  # seconds
    idle_count = 0
    max_idle_backoff = 30.0  # seconds

    while not shutdown_event.is_set():
        # Check kill switch
        async with session_factory() as session:
            if not await is_workers_active(session):
                await asyncio.sleep(poll_interval)
                continue

        # Try to claim work
        async with session_factory() as session:
            job = await claim_job(session, worker_id, job_types)

        if job is None:
            idle_count += 1
            wait = min(poll_interval * (1.5 ** idle_count), max_idle_backoff)
            await asyncio.sleep(wait)
            continue

        idle_count = 0  # Reset on successful claim

        # Dispatch in a bounded task
        await semaphore.acquire()
        task = asyncio.create_task(
            _process_job(session_factory, job, semaphore)
        )
        active_tasks.add(task)
        task.add_done_callback(active_tasks.discard)

    # Graceful shutdown: wait for in-flight tasks
    if active_tasks:
        await asyncio.wait(active_tasks, timeout=60)
```

### Pattern 3: Error Categorization with Closed Enum

**What:** A fixed set of error categories that classifies every failure, rather than free-text error strings.

**When to use:** On every job failure. The `error_category` field is set from this enum.

**Example:**

```python
from enum import StrEnum

class ErrorCategory(StrEnum):
    """Closed set of error categories for failed jobs.

    STANDARDS.md: "Categories are a closed set, defined upfront, extended deliberately."
    Spec reference: Section 3.10 (error_category field).
    """
    # Network / External
    RSS_PARSE = "rss_parse"
    HTTP_TIMEOUT = "http_timeout"
    HTTP_ERROR = "http_error"
    RATE_LIMITED = "rate_limited"
    YOUTUBE_RATE_LIMIT = "youtube_rate_limit"
    API_ERROR = "api_error"

    # Transcription
    TRANSCRIPTION_FAILED = "transcription_failed"
    AUDIO_DOWNLOAD_FAILED = "audio_download_failed"
    AUDIO_CONVERSION_FAILED = "audio_conversion_failed"

    # LLM
    LLM_API_ERROR = "llm_api_error"
    LLM_TIMEOUT = "llm_timeout"
    LLM_PARSE_ERROR = "llm_parse_error"

    # System
    WORKER_TIMEOUT = "worker_timeout"
    DATABASE_ERROR = "database_error"
    PAYLOAD_INVALID = "payload_invalid"
    HANDLER_NOT_FOUND = "handler_not_found"
    UNKNOWN = "unknown"
```

### Pattern 4: Sliding-Window Rate Limiter via PostgreSQL

**What:** Before each external API call, check the `rate_limit_usage` table for calls in the sliding window. If under limit, insert a row and proceed. If over, back off.

**Example:**

```python
async def check_and_acquire_rate_limit(
    session: AsyncSession,
    api_name: str,
    worker_id: str,
    window_minutes: int = 60,
) -> bool:
    """Check rate limit and acquire a slot if available.

    Returns True if the call can proceed, False if rate-limited.
    The caller should back off and retry if False.
    """
    # Count calls in the sliding window
    count_stmt = (
        select(func.count())
        .select_from(RateLimitUsage)
        .where(
            RateLimitUsage.api_name == api_name,
            RateLimitUsage.called_at > text(
                f"NOW() - INTERVAL '{window_minutes} minutes'"
            ),
        )
    )
    result = await session.execute(count_stmt)
    current_count = result.scalar_one()

    # Get the configured limit from system_config
    limit = await get_config_value(session, f"{api_name}_calls_per_hour")

    if current_count >= limit:
        return False

    # Record the call
    usage = RateLimitUsage(
        api_name=api_name,
        worker_id=worker_id,
    )
    session.add(usage)
    await session.commit()
    return True
```

### Pattern 5: Backpressure via Queue Depth Check

**What:** Before executing a discovery/fetch job, check the `process_content` pending queue depth. If above threshold, demote the current job's effective priority.

**Example:**

```python
async def get_effective_priority(
    session: AsyncSession,
    job: Job,
) -> int:
    """Apply backpressure demotion if transcription queue is deep.

    Spec Section 5.8: When process_content queue > max_pending_transcriptions,
    demote discovery/fetch job priority by +3. Restore when < 80% threshold.
    """
    # Only apply to discovery and fetch job types
    BACKPRESSURE_JOB_TYPES = {
        "discover_thinker", "refresh_due_sources",
        "fetch_podcast_feed", "scrape_substack",
        "fetch_youtube_channel", "fetch_guest_feed",
        "discover_guests_listennotes", "discover_guests_podcastindex",
        "search_youtube_appearances", "scan_for_candidates",
    }

    if job.job_type not in BACKPRESSURE_JOB_TYPES:
        return job.priority

    # Count pending transcription jobs
    depth = await _get_queue_depth(session, "process_content")
    threshold = await get_config_value(session, "max_pending_transcriptions")

    if depth > threshold:
        # Demote by 3, cap at 10 (lowest priority)
        return min(job.priority + 3, 10)
    elif depth < threshold * 0.8:
        # Below 80% threshold: normal priority
        return job.priority
    else:
        # In the hysteresis band: maintain current state
        return job.priority
```

### Anti-Patterns to Avoid

- **Holding FOR UPDATE locks during processing:** Never process a job inside the same transaction as the claim. Claim, commit, then process in a new transaction or no transaction.
- **Polling without backoff:** A tight poll loop with no adaptive backoff wastes CPU and database connections when the queue is empty. Use exponential backoff from 2s to 30s.
- **Free-text error categories:** Using arbitrary strings defeats the purpose. Error categories must come from a closed enum. Adding a new category is a code change, not a runtime decision.
- **In-memory rate limiting:** Rate limits tracked per-process are useless with multiple workers. The `rate_limit_usage` table is the coordination point.
- **Checking kill switch on every SQL query:** Check `workers_active` once per poll cycle, not on every database operation. It is a coarse control -- polling every 2-30 seconds is sufficient.
- **Single session for claiming and processing:** The claim session should be short-lived. Processing may need its own session with different transaction isolation or timeout behavior.
- **Raw SQL for everything:** Use SQLAlchemy 2.0 ORM for the claim query and status updates. Raw SQL is fine for the stale reclamation UPDATE (it is a bulk operation), but ORM provides type safety and testability for the hot path.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Job queue contention | Custom locking with advisory locks | `SELECT FOR UPDATE SKIP LOCKED` | PostgreSQL handles lock contention natively; advisory locks have cleanup edge cases |
| Exponential backoff calculation | Custom retry timing | `min(base_delay * 2^attempts, max_delay)` formula | Standard formula; `2^attempts minutes` per spec (2min, 4min, 8min, 16min) |
| Rate limit sliding window | In-memory counter per process | `COUNT(*) WHERE called_at > NOW() - interval` on `rate_limit_usage` table | Coordination across concurrent workers requires shared state; PostgreSQL IS the shared state |
| Worker lifecycle management | Thread pools, multiprocessing | `asyncio.Semaphore` + `asyncio.create_task` | AsyncIO is already the runtime; adding threads introduces complexity and debugging difficulty |
| Graceful shutdown | `os.kill`, `atexit` hacks | `signal.SIGTERM` handler setting an `asyncio.Event` | Standard pattern for async services; works with Docker SIGTERM on Railway |
| Configuration read | File-based config reload | `system_config` table query on each poll cycle | Spec requires runtime-changeable config without redeploy; database is the config store |

**Key insight:** The entire Phase 2 is infrastructure code with no external dependencies beyond PostgreSQL. Every component (claim, retry, rate limit, backpressure, kill switch) is a thin Python layer over SQL queries. The complexity is in getting the transaction boundaries and concurrency semantics right, not in the amount of code.

## Common Pitfalls

### Pitfall 1: Job Claim Index Not Covering the WHERE Clause

**What goes wrong:** The existing `ix_jobs_claim` index is on `(status, priority, scheduled_at)`, but the claim query filters on `status IN ('pending', 'retrying') AND scheduled_at <= NOW()`. Without a partial index, the query scans completed/failed jobs too. As the jobs table grows to 100K+ rows (the spec retains all jobs for auditability), claim latency degrades from <1ms to 50-100ms.

**Why it happens:** The existing index was created in Phase 1 as a general-purpose index. The claim query needs a partial index that only covers claimable rows.

**How to avoid:** Add a partial index via Alembic migration:
```sql
CREATE INDEX ix_jobs_claimable
ON jobs (priority, scheduled_at)
WHERE status IN ('pending', 'retrying');
```
Keep the existing `ix_jobs_claim` for general status queries. The partial index is specifically for the hot claim path.

**Warning signs:** Monitor `EXPLAIN ANALYZE` output for the claim query. If it shows a Seq Scan or Index Scan touching >1000 rows, the partial index is needed.

### Pitfall 2: Stale Reclamation Creates Infinite Retry Loop

**What goes wrong:** A job that always exceeds `stale_job_timeout_minutes` (e.g., a transcription job on a 3-hour podcast with a 30-minute timeout) gets reclaimed, retried, reclaimed, retried indefinitely until `max_attempts` is exhausted. But during each retry cycle, it consumes a worker slot for 30 minutes before being reclaimed -- effectively reducing worker capacity.

**Why it happens:** The stale timeout is a blanket value that does not account for legitimately long-running jobs. The spec's `stale_job_timeout_minutes = 30` is appropriate for most jobs but too short for long audio transcriptions.

**How to avoid:** Allow per-job-type timeout overrides in the job's `payload` or derive from `job_type`. For example, `process_content` jobs could have a 120-minute timeout. The reclamation query should check: `started_at < NOW() - (timeout_for_job_type * INTERVAL '1 minute')`. At minimum, log when a reclaimed job has been reclaimed more than once -- that signals a systemic problem, not a transient crash.

**Warning signs:** Jobs with `error_category = 'worker_timeout'` that have `attempts > 1` and the same `job_type` appearing repeatedly.

### Pitfall 3: Race Condition in Backpressure Check

**What goes wrong:** Two workers both check `process_content` queue depth, both see 499 (just under the threshold of 500), both proceed at normal priority. Meanwhile, 10 new transcription jobs are added. Neither worker applied backpressure because the check was non-transactional.

**Why it happens:** The backpressure check is advisory (like rate limiting). A brief window of inconsistency is acceptable per the spec design ("external APIs have their own enforcement").

**How to avoid:** Accept this as a design choice, not a bug. Backpressure is a soft mechanism -- it slows discovery proportionally, it does not provide hard guarantees. The spec explicitly says "priority demotion instead of pausing" because brief overshoots are acceptable. Do NOT try to make this transactional -- it would add unnecessary contention to the claim path.

### Pitfall 4: Kill Switch Not Checked After Long Processing

**What goes wrong:** Admin sets `workers_active = false` while a worker is processing a job that takes 10 minutes. The worker finishes, immediately claims the next job, and processes it too -- because the kill switch was only checked at the start of the poll cycle.

**How to avoid:** Check `workers_active` both before claiming AND after completing a job, before returning to the claim loop. This adds one extra query per job completion but ensures the kill switch takes effect within one job cycle, not one poll cycle.

### Pitfall 5: Scheduled_at Null Handling

**What goes wrong:** A newly created job has `scheduled_at = NULL` (not yet scheduled). The claim query filters `WHERE scheduled_at <= NOW()`, which returns FALSE for NULL values (NULL is not <= anything). The job is never claimed.

**How to avoid:** The claim query must handle NULL `scheduled_at` by treating it as "immediately eligible": `WHERE (scheduled_at IS NULL OR scheduled_at <= NOW())`. Alternatively, always set `scheduled_at` to `NOW()` when creating a job (the factory already defaults to None -- this needs updating).

## Code Examples

Verified patterns from SQLAlchemy 2.0 docs and PostgreSQL documentation.

### Job Completion

```python
async def complete_job(session: AsyncSession, job_id: uuid.UUID) -> None:
    """Mark a job as successfully completed."""
    stmt = (
        update(Job)
        .where(Job.id == job_id)
        .values(
            status="done",
            completed_at=_now(),
            error=None,
            error_category=None,
        )
    )
    await session.execute(stmt)
    await session.commit()
```

### Job Failure with Retry Decision

```python
async def fail_job(
    session: AsyncSession,
    job_id: uuid.UUID,
    error_msg: str,
    error_category: ErrorCategory,
    max_attempts: int = 3,
) -> None:
    """Mark a job as failed. Retry with backoff if under max_attempts."""
    # Fetch current state
    job = await session.get(Job, job_id)
    if job is None:
        return

    if job.attempts < max_attempts:
        # Retry with exponential backoff: 2^attempts minutes
        backoff_minutes = 2 ** job.attempts
        job.status = "retrying"
        job.scheduled_at = _now() + timedelta(minutes=backoff_minutes)
    else:
        job.status = "failed"
        job.completed_at = _now()

    job.error = error_msg
    job.error_category = error_category.value
    job.last_error_at = _now()
    job.worker_id = None  # Release worker claim

    await session.commit()
```

### Stale Job Reclamation (Raw SQL for Bulk Operation)

```python
async def reclaim_stale_jobs(session: AsyncSession) -> list[dict]:
    """Reclaim jobs stuck in 'running' state beyond the timeout.

    Spec Section 6.3: Runs every 5 minutes in the worker event loop.
    Not a jobs-table job -- runs directly to avoid circular dependency.
    """
    stmt = text("""
        UPDATE jobs
        SET status = CASE
                WHEN attempts + 1 >= max_attempts THEN 'failed'
                ELSE 'retrying'
            END,
            worker_id = NULL,
            attempts = attempts + 1,
            error = 'Reclaimed: exceeded stale_job_timeout_minutes',
            error_category = 'worker_timeout',
            last_error_at = NOW(),
            scheduled_at = CASE
                WHEN attempts + 1 >= max_attempts THEN NULL
                ELSE NOW() + (POWER(2, attempts + 1) * INTERVAL '1 minute')
            END,
            completed_at = CASE
                WHEN attempts + 1 >= max_attempts THEN NOW()
                ELSE NULL
            END
        WHERE status = 'running'
          AND started_at < NOW() - (
              (SELECT (value->>'value')::int FROM system_config
               WHERE key = 'stale_job_timeout_minutes') * INTERVAL '1 minute'
          )
        RETURNING id, job_type, worker_id, attempts, max_attempts
    """)
    result = await session.execute(stmt)
    reclaimed = [dict(row._mapping) for row in result.fetchall()]
    await session.commit()
    return reclaimed
```

### Kill Switch Check

```python
async def is_workers_active(session: AsyncSession) -> bool:
    """Check the global kill switch from system_config.

    Spec Section 3.12: workers_active = false halts all job claiming.
    Workers check this on every poll cycle.
    """
    stmt = select(SystemConfig.value).where(SystemConfig.key == "workers_active")
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()

    if row is None:
        # No config entry = default to active (fail-open)
        return True

    # value is JSONB, could be {"value": true} or just true
    if isinstance(row, dict):
        return bool(row.get("value", True))
    return bool(row)
```

### Handler Registry Pattern

```python
from typing import Protocol
from sqlalchemy.ext.asyncio import AsyncSession

class JobHandler(Protocol):
    """Protocol for job handlers. Every handler must implement this."""

    async def __call__(
        self,
        session: AsyncSession,
        job: Job,
    ) -> None:
        """Process a job. Raise on failure (will be caught and categorized)."""
        ...

# Registry populated by Phase 3+ as handlers are implemented
JOB_HANDLERS: dict[str, JobHandler] = {}

def register_handler(job_type: str, handler: JobHandler) -> None:
    """Register a handler for a job type."""
    JOB_HANDLERS[job_type] = handler
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `Session.query(Job).with_for_update()` | `select(Job).with_for_update(skip_locked=True)` | SQLAlchemy 2.0 (Jan 2023) | Must use 2.0-style select, not legacy query API |
| `Session.query().filter()` | `select().where()` | SQLAlchemy 2.0 | All existing code uses 2.0 style already |
| Redis-backed Celery queues | PostgreSQL SKIP LOCKED | PostgreSQL 9.5 (2016) | No Redis dependency; transactional consistency with app data |
| Thread-based workers | asyncio + Semaphore | Python 3.4+ / mature in 3.12 | Matches existing FastAPI async architecture |
| `loop_scope="function"` | `loop_scope="session"` | pytest-asyncio 0.25+ | Already configured in pyproject.toml |

**Deprecated/outdated:**
- SQLAlchemy `Session.query()` API -- removed in SQLAlchemy 2.1, use `select()` exclusively
- `socket.setdefaulttimeout()` for feedparser -- not needed in Phase 2 (no feed fetching), but note for Phase 3

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest 8.x + pytest-asyncio 0.25+ |
| Config file | `pyproject.toml` (already configured) |
| Quick run command | `uv run pytest tests/unit -x -q` |
| Full suite command | `uv run pytest tests/ -x` |

### Phase Requirements to Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| QUEUE-01 | Job claimed by priority via SELECT FOR UPDATE SKIP LOCKED; no two workers claim same job | integration | `uv run pytest tests/integration/test_claim.py -x` | No -- Wave 0 |
| QUEUE-02 | Worker loop claims, dispatches by type, respects concurrency limit | integration | `uv run pytest tests/integration/test_worker_loop.py -x` | No -- Wave 0 |
| QUEUE-03 | Failed job retried with 2^attempts backoff; stops at max_attempts | unit + integration | `uv run pytest tests/unit/test_retry.py tests/integration/test_claim.py -x` | No -- Wave 0 |
| QUEUE-04 | Stale running jobs reclaimed within 5 minutes, returned to queue | integration | `uv run pytest tests/integration/test_reclaim.py -x` | No -- Wave 0 |
| QUEUE-05 | Rate limit checked via sliding window on rate_limit_usage; backs off when at limit | unit + integration | `uv run pytest tests/unit/test_rate_limiter.py tests/integration/test_rate_limit.py -x` | No -- Wave 0 |
| QUEUE-06 | Discovery priority demoted by +3 when process_content queue > threshold; restored at 80% | unit + integration | `uv run pytest tests/unit/test_backpressure.py tests/integration/test_backpressure.py -x` | No -- Wave 0 |
| QUEUE-07 | workers_active=false halts all job claiming | integration | `uv run pytest tests/integration/test_kill_switch.py -x` | No -- Wave 0 |
| QUEUE-08 | Failed jobs have error_category from closed enum | unit | `uv run pytest tests/unit/test_errors.py -x` | No -- Wave 0 |
| QUAL-04 | Every job handler has a contract test (handler interface + dispatch) | unit | `uv run pytest tests/contract/ -x` | No -- Wave 0 |

### Sampling Rate

- **Per task commit:** `uv run pytest tests/unit -x -q` (should complete <5s)
- **Per wave merge:** `uv run pytest tests/ -x` (full suite, should complete <60s)
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps

- [ ] `tests/unit/test_retry.py` -- covers QUEUE-03 (pure backoff math)
- [ ] `tests/unit/test_backpressure.py` -- covers QUEUE-06 (priority demotion logic)
- [ ] `tests/unit/test_errors.py` -- covers QUEUE-08 (error categorization)
- [ ] `tests/unit/test_rate_limiter.py` -- covers QUEUE-05 (sliding window math)
- [ ] `tests/integration/test_claim.py` -- covers QUEUE-01 (concurrent claim safety)
- [ ] `tests/integration/test_reclaim.py` -- covers QUEUE-04 (stale job reclamation)
- [ ] `tests/integration/test_kill_switch.py` -- covers QUEUE-07 (kill switch behavior)
- [ ] `tests/integration/test_rate_limit.py` -- covers QUEUE-05 (rate_limit_usage table)
- [ ] `tests/integration/test_backpressure.py` -- covers QUEUE-06 (queue depth queries)
- [ ] `tests/integration/test_worker_loop.py` -- covers QUEUE-02 (full loop lifecycle)
- [ ] `tests/contract/test_handler_contracts.py` -- covers QUAL-04 (handler protocol)
- [ ] `tests/contract/__init__.py` -- new test directory

## Open Questions

1. **Per-job-type stale timeout**
   - What we know: The spec defines a single `stale_job_timeout_minutes = 30` in system_config. The spec also says `process_content` has a different `max_attempts` (2 vs default 3).
   - What's unclear: Should `stale_job_timeout_minutes` also be per-type? A GPU transcription of a 3-hour podcast legitimately takes >30 minutes.
   - Recommendation: Implement a per-type timeout override in the job's payload (e.g., `payload.timeout_minutes`) falling back to the system_config default. This keeps the system_config as the global default while allowing specific jobs to declare longer timeouts. GPU workers can set `timeout_minutes: 120` for transcription jobs.

2. **Backpressure hysteresis state**
   - What we know: Spec says demote when depth > threshold, restore when depth < 80% of threshold.
   - What's unclear: Should the "currently in backpressure" state be persisted anywhere, or is it purely computed on each check?
   - Recommendation: Purely computed on each check. No state needed. The threshold check is stateless by design -- if the queue depth is above threshold, demote; if below 80%, don't. The 80%-100% range is a natural hysteresis band where the current priority (demoted or normal) persists because neither trigger fires. Adding persisted state would create a coordination problem across workers.

3. **Worker ID format**
   - What we know: The spec uses `worker_id` as TEXT on jobs. The `RateLimitUsage` model also has `worker_id`.
   - What's unclear: What format should worker IDs take?
   - Recommendation: `{service_type}-{hostname}-{pid}` e.g., `cpu-worker-abc123-42`. This provides enough information to identify which instance claimed a job without requiring a worker registry table.

4. **SystemConfig value format**
   - What we know: `SystemConfig.value` is JSONB. The spec shows values like `true`, `20`, `500`.
   - What's unclear: Are values stored as `{"value": true}` or just `true` directly?
   - Recommendation: Store the value directly as the JSONB value (e.g., `true`, `500`, `["trailer", "teaser"]`). The JSONB column handles all JSON types natively. Wrapping in `{"value": ...}` adds unnecessary nesting. The factory already uses `{"enabled": True}` -- adjust to match whichever pattern is chosen and be consistent.

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| QUEUE-01 | DB-backed job queue using `SELECT FOR UPDATE SKIP LOCKED` with priority ordering | Pattern 1 (Two-Transaction Claim), partial index recommendation, SQLAlchemy `with_for_update(skip_locked=True)` verified |
| QUEUE-02 | Async worker base loop that claims and dispatches jobs by type with configurable concurrency | Pattern 2 (Async Worker Loop), `asyncio.Semaphore` for concurrency, handler registry pattern |
| QUEUE-03 | Job retry with exponential backoff and per-type max attempt limits | Backoff formula `2^attempts minutes`, fail_job code example, per-type max_attempts from spec (process_content: 2, feed fetches: 4, default: 3) |
| QUEUE-04 | Stale job reclamation running every 5 minutes, returning stuck running jobs to queued | Reclamation SQL from spec Section 6.3, runs in worker event loop (not as a job), marks failed if at max_attempts |
| QUEUE-05 | Rate limit coordination via rate_limit_usage table with sliding-window queries | Pattern 4 (Sliding-Window Rate Limiter), existing RateLimitUsage model and ix_rate_limit_usage_window index |
| QUEUE-06 | Backpressure mechanism demoting discovery priority when transcription queue exceeds threshold | Pattern 5 (Backpressure), spec Section 5.8 details (+3 demotion, 80% restore threshold, hysteresis band) |
| QUEUE-07 | Global kill switch (workers_active = false in system_config) halting all job claiming | Kill switch code example, check on every poll cycle + after job completion, fail-open if config missing |
| QUEUE-08 | Error categorization with closed set of error categories on failed jobs | Pattern 3 (ErrorCategory StrEnum), STANDARDS.md requirement for closed set, all categories from spec enumerated |
| QUAL-04 | Contract tests for every job handler (given input payload, expected side effects) | Handler protocol (JobHandler), registry pattern, contract test directory structure, tests verify dispatch + interface conformance |
</phase_requirements>

## Sources

### Primary (HIGH confidence)

- [SQLAlchemy 2.0 Discussion #10460](https://github.com/sqlalchemy/sqlalchemy/discussions/10460) -- Confirmed `select().with_for_update(skip_locked=True)` generates correct PostgreSQL SQL
- [SQLAlchemy 2.0 Selectable Docs](https://docs.sqlalchemy.org/en/20/core/selectable.html) -- `with_for_update()` API reference, `skip_locked` parameter
- [Neon PostgreSQL Queue Guide](https://neon.com/guides/queue-system) -- CTE pattern for atomic claim-and-update, partial index recommendation
- [Vlad Mihalcea: Database Job Queue SKIP LOCKED](https://vladmihalcea.com/database-job-queue-skip-locked/) -- Authoritative SKIP LOCKED patterns, transaction boundary best practices
- [Inferable: Unreasonable Effectiveness of SKIP LOCKED](https://www.inferable.ai/blog/posts/postgres-skip-locked) -- Production patterns, performance characteristics, throughput benchmarks
- ThinkTank Specification Sections 3.10, 3.12, 3.13, 5.8, 6.1-6.5 -- Authoritative requirements for all queue behavior

### Secondary (MEDIUM confidence)

- [Netdata: FOR UPDATE SKIP LOCKED Workflows](https://www.netdata.cloud/academy/update-skip-locked/) -- Queue workflow patterns without deadlocks
- [Renegade Otter: Job Queues with Postgres](https://renegadeotter.com/2023/11/30/job-queues-with-postrgres.html) -- Production patterns, failure handling
- [SQLAlchemy mailing list: CTE + SKIP LOCKED](https://www.mail-archive.com/sqlalchemy@googlegroups.com/msg43754.html) -- CTE interaction with SKIP LOCKED in SQLAlchemy

### Tertiary (LOW confidence)

- Per-job-type stale timeout: Inferred recommendation, not in spec. Validate with user before implementing.
- Worker ID format: Convention recommendation, not specified. Any unique-per-process string works.

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- zero new dependencies, all existing libraries verified
- Architecture: HIGH -- `SELECT FOR UPDATE SKIP LOCKED` is the most well-documented PostgreSQL queue pattern; multiple production reference implementations exist (Solid Queue, PGQueuer, Procrastinate, Graphile Worker)
- Pitfalls: HIGH -- connection pool exhaustion (Pitfall #2 from project research), jobs table bloat (Pitfall #8), priority starvation (Pitfall #14) are all documented with mitigation strategies
- Code examples: HIGH -- SQLAlchemy 2.0 `with_for_update(skip_locked=True)` verified against official docs and GitHub discussions

**Research date:** 2026-03-08
**Valid until:** 2026-04-08 (stable domain, no fast-moving dependencies)
