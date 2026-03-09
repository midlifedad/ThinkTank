# Phase 5: LLM Governance - Research

**Researched:** 2026-03-08
**Domain:** LLM integration (Anthropic Claude API), approval workflows, scheduled tasks, audit trails
**Confidence:** HIGH

## Summary

Phase 5 introduces the LLM Supervisor layer that governs all corpus expansion decisions. The core technical challenge is integrating the Anthropic Claude API (async client) into the existing job handler/worker architecture to produce structured approval decisions with full audit trails. The system must handle API unavailability gracefully by escalating timed-out reviews to human review rather than blocking the pipeline.

The project already has all the infrastructure this phase needs: the `llm_reviews` table and ORM model, the `LLMReview` factory, error categories for LLM failures (`LLM_API_ERROR`, `LLM_TIMEOUT`, `LLM_PARSE_ERROR`), the `awaiting_llm` and `rejected_by_llm` job statuses, the `llm_review_id` foreign key on both `Job` and `CandidateThinker`, the `approval_status` fields on `Thinker`/`Source`/`CandidateThinker`, and the scheduler pattern from reclamation/GPU scaling. The primary new code is: (1) an Anthropic API client wrapper, (2) context snapshot builders (bounded DB queries), (3) prompt templates and response parsers, (4) approval handler logic that updates entity statuses, (5) timeout escalation checker, and (6) three scheduled task types (health check, digest, weekly audit).

**Primary recommendation:** Use the `anthropic` Python SDK with `AsyncAnthropic` client and Pydantic-based structured outputs (`messages.parse()`) for guaranteed JSON schema compliance. Follow the existing handler + scheduler patterns exactly.

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| GOV-01 | LLM Supervisor using Claude claude-sonnet-4-20250514 with structured JSON prompts | Anthropic SDK `AsyncAnthropic` + `messages.parse()` with Pydantic models for each review type |
| GOV-02 | Thinker approval flow with context snapshot before activation | Context snapshot builder querying thinkers/sources/categories + `llm_approval_check` handler |
| GOV-03 | Source approval flow before RSS polling begins | Same approval handler pattern, different context snapshot and Pydantic response schema |
| GOV-04 | Candidate thinker batch review exceeding appearance threshold | Batch review handler with candidate context builder, same structured output pattern |
| GOV-05 | Full audit trail in `llm_reviews` table | Every LLM call writes prompt, raw response, parsed decision, reasoning, tokens, duration to `LLMReview` row |
| GOV-06 | Fallback/timeout escalation after `llm_timeout_hours` | Timeout escalation scheduler (runs every 15 min) checking `awaiting_llm` job age |
| GOV-07 | Graceful degradation when Anthropic API unavailable | SDK exception handling, retry via job queue, escalation to human after max retries |
| GOV-08 | Scheduled health checks (6h), daily digests (07:00 UTC), weekly audits (Mondays) | Three scheduler coroutines in worker loop, same pattern as `_reclamation_scheduler` |
| GOV-09 | Context budgeting with bounded snapshots (max 50 thinkers, 100 errors, 20 candidates) | Bounded SQL queries with `.limit()`, token tracking via `message.usage.input_tokens + output_tokens` |
| DISC-06 | Candidate-to-thinker promotion flow triggered by LLM batch review | Approval handler creates `Thinker` + `Source` rows from approved `CandidateThinker`, updates status |
</phase_requirements>

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| `anthropic` | >=0.84.0 | Anthropic Claude API client | Official SDK. AsyncAnthropic for async, messages.parse() for structured output, built-in retry. |
| `pydantic` | >=2.12.5 (already installed) | Response schema definitions | Already in project. SDK's `.parse()` accepts Pydantic models directly for structured outputs. |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `sqlalchemy[asyncio]` | >=2.0.46 (already installed) | DB queries for context snapshots | All bounded queries for snapshot builders |
| `structlog` | >=25.5.0 (already installed) | Structured logging with correlation IDs | All LLM operations logged with job_id, review_type |
| `httpx` | >=0.28.1 (already installed) | Not directly needed (SDK handles HTTP) | Already a transitive dep of anthropic SDK |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| `anthropic` SDK | Raw httpx calls to API | Lose automatic retry, structured outputs, type safety. No benefit. |
| Pydantic structured outputs | Manual JSON parsing + json.loads | Lose schema guarantee. The spec says "All LLM responses are parsed as JSON. If parsing fails, escalate_to_human." SDK eliminates most parse failures. |
| `messages.parse()` | `messages.create()` + manual validation | Lower-level control but more code. `.parse()` is the recommended approach. |

**Installation:**
```bash
uv add anthropic
```

## Architecture Patterns

### Recommended Project Structure
```
src/thinktank/
  llm/                        # NEW: LLM Supervisor module
    __init__.py
    client.py                 # AsyncAnthropic wrapper (singleton, config)
    schemas.py                # Pydantic response models for each review type
    prompts.py                # System prompt + per-review-type prompt templates
    snapshots.py              # Context snapshot builders (bounded DB queries)
    decisions.py              # Decision application logic (update thinker/source/candidate status)
  handlers/
    llm_approval_check.py     # NEW: Handler for llm_approval_check jobs
    llm_health_check.py       # NEW: Handler for llm_health_check jobs (or combine into one)
  worker/
    loop.py                   # MODIFIED: Add LLM scheduler tasks
```

### Pattern 1: Anthropic Client Wrapper
**What:** Thin async wrapper around `AsyncAnthropic` that reads `ANTHROPIC_API_KEY` from env, configures timeouts and retries, and provides a `review()` method returning structured output.
**When to use:** Every LLM call goes through this wrapper.
**Example:**
```python
# Source: Anthropic SDK docs + project config.py pattern
import os
from anthropic import AsyncAnthropic
from pydantic import BaseModel

class LLMClient:
    """Thin wrapper for Anthropic API calls with project-specific defaults."""

    def __init__(self) -> None:
        self._client = AsyncAnthropic(
            api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
            max_retries=2,
            timeout=120.0,  # 2 minute timeout per call
        )
        self.model = "claude-sonnet-4-20250514"

    async def review(
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: type[BaseModel],
        max_tokens: int = 4096,
    ) -> tuple[BaseModel, int, int]:
        """Call Claude and return (parsed_output, input_tokens, output_tokens, duration_ms)."""
        import time
        start = time.monotonic()
        response = await self._client.messages.parse(
            model=self.model,
            max_tokens=max_tokens,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
            output_format=response_model,
        )
        duration_ms = int((time.monotonic() - start) * 1000)
        tokens = response.usage.input_tokens + response.usage.output_tokens
        return response.parsed_output, tokens, duration_ms
```

### Pattern 2: Pydantic Response Schemas (One Per Review Type)
**What:** Each review type has a Pydantic model defining the expected JSON structure.
**When to use:** Passed to `messages.parse()` as `output_format`.
**Example:**
```python
# Source: Spec Section 8.1 + Anthropic structured outputs docs
from pydantic import BaseModel
from typing import Literal

class ThinkerApprovalResponse(BaseModel):
    decision: Literal["approved", "rejected", "approved_with_modifications", "escalate_to_human"]
    reasoning: str
    modifications: dict | None = None  # e.g. {"approved_backfill_days": 90}
    flagged_items: list[str] | None = None

class SourceApprovalResponse(BaseModel):
    decision: Literal["approved", "rejected", "approved_with_modifications", "escalate_to_human"]
    reasoning: str
    approved_backfill_days: int | None = None
    modifications: dict | None = None

class CandidateApprovalResponse(BaseModel):
    decision: Literal["approved", "rejected", "duplicate", "need_more_appearances", "escalate_to_human"]
    reasoning: str
    tier: int | None = None  # 1, 2, or 3 if approved
    categories: list[str] | None = None
    initial_sources: list[str] | None = None
    duplicate_of: str | None = None  # existing thinker slug if duplicate

class HealthCheckResponse(BaseModel):
    status: Literal["healthy", "issues_detected"]
    findings: list[str]
    recommended_actions: list[dict] | None = None
    config_adjustments: dict | None = None  # system_config changes within bounds
```

### Pattern 3: Bounded Context Snapshots
**What:** SQL queries with `.limit()` caps matching spec (50 thinkers, 100 errors, 20 candidates).
**When to use:** Before every LLM call to build the `context_snapshot` JSONB.
**Example:**
```python
# Source: Spec Section 8.1 + existing backpressure.py query pattern
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

async def build_thinker_approval_context(
    session: AsyncSession,
    thinker_id: uuid.UUID,
) -> dict:
    """Build bounded context for a thinker approval review."""
    # Load the proposed thinker with sources and categories
    thinker = await session.get(Thinker, thinker_id)

    # Corpus stats (bounded)
    total_thinkers = await session.scalar(
        select(func.count()).select_from(Thinker)
        .where(Thinker.approval_status == "approved")
    )
    # ... queue depth, etc.

    return {
        "proposed_thinker": { ... },
        "corpus_stats": {
            "total_approved_thinkers": total_thinkers,
            "total_content": ...,
            "queue_depth": ...,
        },
    }
```

### Pattern 4: Scheduler Coroutines (Same as Reclamation/GPU Scaling)
**What:** Background tasks in the worker loop using `_interruptible_sleep`.
**When to use:** Health checks (every 6h), timeout escalation (every 15m), digests.
**Example:**
```python
# Source: Existing worker/loop.py _reclamation_scheduler pattern
async def _llm_timeout_escalation_scheduler(
    session_factory: async_sessionmaker[AsyncSession],
    interval: float,  # 900 seconds = 15 minutes
    shutdown_event: asyncio.Event,
) -> None:
    """Check for awaiting_llm jobs that exceeded timeout and escalate."""
    while not shutdown_event.is_set():
        try:
            await _interruptible_sleep(interval, shutdown_event)
            if shutdown_event.is_set():
                break
            async with session_factory() as session:
                escalated = await escalate_timed_out_reviews(session)
                if escalated:
                    logger.info("llm_timeout_escalated", count=escalated)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("llm_timeout_escalation_failed")
```

### Pattern 5: Job Handler for LLM Approval
**What:** A handler registered for `llm_approval_check` job type. Reads the pending job, builds context, calls LLM, applies decision.
**When to use:** Triggered when any entity enters `awaiting_llm` status.
**Example:**
```python
# Source: Existing handler pattern (fetch_podcast_feed, process_content)
async def handle_llm_approval_check(session: AsyncSession, job: Job) -> None:
    """Process an LLM approval check job."""
    review_type = job.payload.get("review_type")  # "thinker_approval", etc.
    target_id = uuid.UUID(job.payload["target_id"])
    pending_job_id = job.payload.get("pending_job_id")  # Job waiting for approval

    # Build context snapshot
    context = await build_context_snapshot(session, review_type, target_id)

    # Build prompt
    system_prompt, user_prompt = build_prompt(review_type, context)

    # Call LLM
    try:
        result, tokens, duration_ms = await llm_client.review(
            system_prompt, user_prompt, get_response_schema(review_type)
        )
    except anthropic.APIError:
        raise  # Worker loop categorizes and retries

    # Log to llm_reviews
    review = LLMReview(
        review_type=review_type,
        trigger="job_gate",
        context_snapshot=context,
        prompt_used=f"{system_prompt}\n\n{user_prompt}",
        llm_response=result.model_dump_json(),
        decision=result.decision,
        decision_reasoning=result.reasoning,
        modifications=result.modifications if hasattr(result, 'modifications') else None,
        model=llm_client.model,
        tokens_used=tokens,
        duration_ms=duration_ms,
    )
    session.add(review)
    await session.flush()

    # Apply decision to target entity and pending job
    await apply_decision(session, review_type, target_id, pending_job_id, result, review.id)
    await session.commit()
```

### Anti-Patterns to Avoid
- **Unbounded context snapshots:** Never load all thinkers/errors/candidates. Always use `.limit()` matching spec bounds (50/100/20).
- **Blocking the worker loop for LLM calls:** LLM calls take seconds. They run as normal jobs through the handler system (which uses asyncio tasks with semaphore), not inline in the scheduler.
- **Retrying LLM parse failures:** If `messages.parse()` raises a parse error, escalate to human immediately. Do NOT retry -- the model's response structure won't improve on retry.
- **Storing API key in config.py:** Use `ANTHROPIC_API_KEY` env var only. The Anthropic SDK auto-reads it when no key is passed, but explicit is better.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| JSON schema enforcement on LLM output | Custom JSON parsing + validation | `messages.parse()` with Pydantic models | SDK uses constrained decoding -- model literally cannot produce invalid JSON. Eliminates entire class of parse errors. |
| Retry logic for API calls | Custom exponential backoff for Anthropic | SDK built-in retry (`max_retries=2`) | SDK handles 429, 5xx, connection errors with jitter. Only need job-level retry for total failures. |
| Token counting | Manual tokenizer or estimation | `response.usage.input_tokens + output_tokens` | Exact counts returned on every API response. No estimation needed. |
| Cron scheduling | `croniter` or `apscheduler` | `_interruptible_sleep` with interval math | Project already has this pattern. Health check = sleep 21600s, digest = compute seconds until 07:00 UTC. Keep it consistent. |
| LLM exception categorization | Custom exception mapping | Extend `categorize_error()` for `anthropic.*Error` types | Existing error categorization system handles this. Just add isinstance checks. |

**Key insight:** The Anthropic SDK handles HTTP-level concerns (retry, timeout, rate limiting). The job queue handles application-level concerns (retry after total failure, escalation). Don't duplicate either layer.

## Common Pitfalls

### Pitfall 1: Timezone Mismatch in Timeout Calculations
**What goes wrong:** Using `datetime.utcnow()` or `datetime.now(UTC)` with timezone info when comparing against TIMESTAMP WITHOUT TIME ZONE columns.
**Why it happens:** The project uses timezone-naive datetimes throughout (Phase 1 decision). PostgreSQL `LOCALTIMESTAMP` is also timezone-naive.
**How to avoid:** Use the existing `_now()` pattern: `datetime.now(UTC).replace(tzinfo=None)`. For SQL comparisons, use `LOCALTIMESTAMP` (matching reclaim.py).
**Warning signs:** asyncpg `TypeError: can't compare offset-naive and offset-aware datetimes`.

### Pitfall 2: Structured Output Schema Changes
**What goes wrong:** Changing a Pydantic response schema breaks existing `llm_reviews` rows that store the old schema's responses.
**Why it happens:** `llm_response` stores the raw JSON string. If the schema changes, historical rows have different shapes.
**How to avoid:** Store raw response as TEXT (already done). Schema versioning is not needed for v1 -- the `llm_response` field is archival. Parsing always uses the current schema for current decisions only.
**Warning signs:** None in v1. Becomes relevant if we ever need to re-process historical reviews.

### Pitfall 3: LLM Client Lifecycle
**What goes wrong:** Creating a new `AsyncAnthropic()` client per request wastes connection pool resources. Or conversely, sharing a client across asyncio tasks without understanding its thread-safety.
**Why it happens:** `AsyncAnthropic` creates an internal httpx client with a connection pool.
**How to avoid:** Create one `LLMClient` instance in the handler module (module-level or passed via handler factory). The Anthropic SDK's async client is safe for concurrent use within a single event loop.
**Warning signs:** Connection pool exhaustion, excessive TCP connections.

### Pitfall 4: Rate Limiting the Anthropic API
**What goes wrong:** Making too many LLM calls, hitting rate limits, and not coordinating across workers.
**Why it happens:** Multiple workers could trigger LLM approval checks simultaneously.
**How to avoid:** Use the existing `rate_limiter.py` pattern -- add `anthropic_calls_per_hour` to system_config and call `check_and_acquire_rate_limit(session, "anthropic", worker_id)` before each LLM call. The spec lists `anthropic` as a rate-limited API name in Section 3.13.
**Warning signs:** Frequent `RateLimitError` from the SDK despite built-in retry.

### Pitfall 5: Forgetting to Link llm_review_id Back to Job and Entity
**What goes wrong:** The approval decision is logged but the pending job's `llm_review_id` field and the entity's `llm_review_id` (on CandidateThinker) aren't updated.
**Why it happens:** Multiple foreign keys need updating in the same transaction.
**How to avoid:** The `apply_decision()` function must update: (1) the target entity's `approval_status`, (2) the pending job's `status` and `llm_review_id`, (3) for candidates, the `llm_review_id` and `reviewed_by`/`reviewed_at` fields.
**Warning signs:** `llm_review_id` is NULL on jobs that went through LLM review.

### Pitfall 6: Scheduled Task Time Drift
**What goes wrong:** Daily digest at 07:00 UTC drifts because `_interruptible_sleep` accumulates small timing errors over days.
**Why it happens:** Using a fixed interval (86400s) instead of computing "seconds until next 07:00 UTC".
**How to avoid:** For time-of-day schedules (digest, weekly audit), compute the exact seconds until the next target time on each iteration. For interval-based schedules (health check every 6h), the drift is acceptable.
**Warning signs:** Digest fires at 07:12 UTC after a week of drift.

## Code Examples

### Anthropic SDK: Async Client with Structured Output
```python
# Source: Anthropic SDK docs (https://platform.claude.com/docs/en/build-with-claude/structured-outputs)
import os
from anthropic import AsyncAnthropic
from pydantic import BaseModel
from typing import Literal

class ApprovalDecision(BaseModel):
    decision: Literal["approved", "rejected", "approved_with_modifications", "escalate_to_human"]
    reasoning: str
    modifications: dict | None = None

client = AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"], max_retries=2)

response = await client.messages.parse(
    model="claude-sonnet-4-20250514",
    max_tokens=4096,
    system="You are the ThinkTank Supervisor...",
    messages=[{"role": "user", "content": json.dumps(context_snapshot)}],
    output_format=ApprovalDecision,
)

# response.parsed_output is an ApprovalDecision instance
decision = response.parsed_output
tokens = response.usage.input_tokens + response.usage.output_tokens
```

### Error Handling for Anthropic API Calls
```python
# Source: Anthropic SDK exception hierarchy
import anthropic

try:
    result = await llm_client.review(system_prompt, user_prompt, ResponseSchema)
except anthropic.RateLimitError:
    # SDK already retried max_retries times. Re-raise for job queue retry.
    raise
except anthropic.APIConnectionError:
    # Network issue. Re-raise for job queue retry.
    raise
except anthropic.APIStatusError as e:
    if e.status_code >= 500:
        raise  # Server error, retryable
    # 4xx errors (auth, bad request) are not retryable
    raise
```

### Extending categorize_error for Anthropic Exceptions
```python
# Source: Existing queue/errors.py pattern
import anthropic

def categorize_error(exc: Exception) -> ErrorCategory:
    # ... existing checks ...
    if isinstance(exc, anthropic.RateLimitError):
        return ErrorCategory.LLM_API_ERROR
    if isinstance(exc, (anthropic.APIConnectionError, anthropic.APITimeoutError)):
        return ErrorCategory.LLM_TIMEOUT
    if isinstance(exc, anthropic.APIStatusError):
        return ErrorCategory.LLM_API_ERROR
    # Parse errors from structured output
    if isinstance(exc, anthropic.LLMParseError):  # or similar
        return ErrorCategory.LLM_PARSE_ERROR
    # ... rest of existing checks ...
    return ErrorCategory.UNKNOWN
```

### Timeout Escalation Query
```python
# Source: Spec Section 8.6, matching reclaim.py raw SQL pattern
from sqlalchemy import text

async def escalate_timed_out_reviews(session: AsyncSession) -> int:
    """Find and escalate awaiting_llm jobs past timeout."""
    timeout_hours = await get_config_value(session, "llm_timeout_hours", 2)

    # Find timed-out jobs
    stmt = text("""
        UPDATE jobs
        SET payload = jsonb_set(
            COALESCE(payload, '{}'::jsonb),
            '{needs_human_review}',
            'true'::jsonb
        )
        WHERE status = 'awaiting_llm'
          AND created_at < LOCALTIMESTAMP - MAKE_INTERVAL(hours => :timeout_hours)
          AND NOT COALESCE((payload->>'needs_human_review')::boolean, false)
        RETURNING id, job_type
    """)
    result = await session.execute(stmt, {"timeout_hours": timeout_hours})
    escalated = result.fetchall()

    # Create escalation review entries
    for row in escalated:
        review = LLMReview(
            review_type="timeout_escalation",
            trigger="scheduled",
            context_snapshot={"job_id": str(row.id), "job_type": row.job_type},
            prompt_used="N/A - timeout escalation",
            decision="escalate_to_human",
            decision_reasoning=f"LLM API unavailable for >{timeout_hours}h. Escalated to human review.",
        )
        session.add(review)

    await session.flush()
    return len(escalated)
```

### Computing Seconds Until Next Target Time
```python
# Source: Standard Python datetime pattern for daily/weekly schedules
from datetime import UTC, datetime, timedelta

def seconds_until_next_utc_hour(target_hour: int) -> float:
    """Compute seconds until the next occurrence of target_hour UTC."""
    now = datetime.now(UTC)
    target = now.replace(hour=target_hour, minute=0, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()

def seconds_until_next_monday_utc(target_hour: int) -> float:
    """Compute seconds until next Monday at target_hour UTC."""
    now = datetime.now(UTC)
    days_until_monday = (7 - now.weekday()) % 7
    if days_until_monday == 0 and (now.hour > target_hour or
        (now.hour == target_hour and now.minute > 0)):
        days_until_monday = 7
    target = (now + timedelta(days=days_until_monday)).replace(
        hour=target_hour, minute=0, second=0, microsecond=0
    )
    return (target - now).total_seconds()
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Manual JSON parsing of LLM output | Pydantic structured outputs via `messages.parse()` | Nov 2025 (GA early 2026) | Eliminates parse errors entirely. Model uses constrained decoding. |
| `output_format` parameter | `output_config.format` (SDK still accepts `output_format`) | Late 2025 | Use `output_format` with `.parse()` -- it's the high-level API and still supported |
| Beta header `structured-outputs-2025-11-13` | No header needed (GA) | Early 2026 | Simplifies integration, no beta flags |
| `anthropic.count_tokens()` (beta) | `response.usage` on every response | Stable | Token counting is always available on message responses. Pre-call counting via `messages.count_tokens()` also available. |

**Deprecated/outdated:**
- `output_format` at top level: Still works during transition but `output_config.format` is the new canonical form. SDK `.parse()` abstracts this.
- Beta token counting: `client.beta.messages.count_tokens()` still works but `messages.count_tokens()` is now stable.

## Open Questions

1. **Anthropic SDK version for `messages.parse()` support**
   - What we know: `messages.parse()` with Pydantic is available in recent SDK versions. PyPI shows 0.84.0 as latest.
   - What's unclear: Exact minimum version that supports `output_format` with `.parse()` in GA (not beta).
   - Recommendation: Pin `anthropic>=0.50.0` as a safe floor. The feature went GA in late 2025; any recent version works.

2. **Exact exception class for structured output parse failures**
   - What we know: When the model response doesn't match the schema despite constrained decoding, an exception is raised.
   - What's unclear: Whether it's `anthropic.LLMParseError`, `pydantic.ValidationError`, or something else.
   - Recommendation: In the `categorize_error` extension, catch both `pydantic.ValidationError` and any anthropic-specific parse error. Test empirically in integration tests.

3. **Batch candidate review vs individual**
   - What we know: Spec says "batch review of candidates exceeding appearance threshold" (GOV-04).
   - What's unclear: Whether to send all pending candidates in one LLM call or one-per-call.
   - Recommendation: One LLM call with all pending candidates (up to 20 per context budget). More cost-efficient and the LLM can cross-reference candidates against each other for dedup.

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 8.0+ with pytest-asyncio 0.25+ |
| Config file | `pyproject.toml` [tool.pytest.ini_options] |
| Quick run command | `uv run pytest tests/unit/ -x -q` |
| Full suite command | `uv run pytest tests/ -x` |

### Phase Requirements -> Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| GOV-01 | LLM client calls Claude, returns structured response | unit | `uv run pytest tests/unit/test_llm_client.py -x` | Wave 0 |
| GOV-02 | Thinker approval: awaiting_llm -> approved/rejected via LLM | integration | `uv run pytest tests/integration/test_llm_approval.py::test_thinker_approval -x` | Wave 0 |
| GOV-03 | Source approval: awaiting_llm -> approved/rejected via LLM | integration | `uv run pytest tests/integration/test_llm_approval.py::test_source_approval -x` | Wave 0 |
| GOV-04 | Candidate batch review: pending_llm candidates reviewed | integration | `uv run pytest tests/integration/test_llm_approval.py::test_candidate_batch_review -x` | Wave 0 |
| GOV-05 | Every LLM call creates llm_reviews row with all fields | integration | `uv run pytest tests/integration/test_llm_approval.py::test_audit_trail -x` | Wave 0 |
| GOV-06 | Timed-out awaiting_llm jobs escalated to human | integration | `uv run pytest tests/integration/test_llm_escalation.py -x` | Wave 0 |
| GOV-07 | API unavailability: jobs retry then escalate | unit | `uv run pytest tests/unit/test_llm_client.py::test_api_unavailable -x` | Wave 0 |
| GOV-08 | Health check / digest / audit scheduled and produce reviews | integration | `uv run pytest tests/integration/test_llm_scheduled.py -x` | Wave 0 |
| GOV-09 | Context snapshots bounded (50/100/20) and tokens tracked | unit + integration | `uv run pytest tests/unit/test_snapshots.py -x` | Wave 0 |
| DISC-06 | Candidate approved -> thinker + source created | integration | `uv run pytest tests/integration/test_llm_approval.py::test_candidate_promotion -x` | Wave 0 |

### Sampling Rate
- **Per task commit:** `uv run pytest tests/unit/ -x -q`
- **Per wave merge:** `uv run pytest tests/ -x`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `tests/unit/test_llm_client.py` -- covers GOV-01, GOV-07 (mocked Anthropic SDK)
- [ ] `tests/unit/test_snapshots.py` -- covers GOV-09 (bounded query assertions)
- [ ] `tests/unit/test_prompts.py` -- covers prompt template generation (pure logic)
- [ ] `tests/unit/test_decisions.py` -- covers decision application logic (pure logic)
- [ ] `tests/unit/test_time_utils.py` -- covers seconds_until_next_utc_hour, etc. (pure logic)
- [ ] `tests/integration/test_llm_approval.py` -- covers GOV-02, GOV-03, GOV-04, GOV-05, DISC-06
- [ ] `tests/integration/test_llm_escalation.py` -- covers GOV-06
- [ ] `tests/integration/test_llm_scheduled.py` -- covers GOV-08
- [ ] `tests/contract/test_llm_handlers.py` -- contract tests for llm_approval_check handler
- [ ] `anthropic` dependency: `uv add anthropic`

## Sources

### Primary (HIGH confidence)
- Anthropic SDK README + structured outputs docs (https://platform.claude.com/docs/en/build-with-claude/structured-outputs) -- AsyncAnthropic API, messages.parse(), Pydantic models, error handling
- Anthropic SDK PyPI (https://pypi.org/project/anthropic/) -- version 0.84.0, Python >=3.9
- ThinkTank Specification Section 8 (LLM Supervisor) -- complete prompt design, approval flows, scheduled checks, fallback behavior
- ThinkTank Specification Section 3.11 (llm_reviews table) -- audit trail schema
- Existing codebase: worker/loop.py, queue/errors.py, handlers/base.py, ingestion/config_reader.py, scaling/railway.py -- established patterns

### Secondary (MEDIUM confidence)
- Anthropic SDK DeepWiki (https://deepwiki.com/anthropics/anthropic-sdk-python) -- exception hierarchy, retry behavior, verified against official README

### Tertiary (LOW confidence)
- Exact minimum SDK version for GA structured outputs -- conservatively set at >=0.50.0, actual minimum may be lower

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- Anthropic SDK is the only option, well-documented, actively maintained
- Architecture: HIGH -- follows existing handler/scheduler patterns exactly, spec is detailed
- Pitfalls: HIGH -- timezone, lifecycle, rate limiting pitfalls are well-established from prior phases
- Context snapshots: HIGH -- bounded queries are simple SQL with `.limit()`, spec gives exact bounds
- Scheduled tasks: HIGH -- identical pattern to reclamation/GPU scaling, already proven in Phase 2/4

**Research date:** 2026-03-08
**Valid until:** 2026-04-08 (stable -- Anthropic SDK and project patterns are well-established)
