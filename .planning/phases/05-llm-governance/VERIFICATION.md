---
phase: 05-llm-governance
verified: 2026-03-09T06:00:00Z
status: passed
score: 5/5 must-haves verified
re_verification: false
---

# Phase 5: LLM Governance Verification Report

**Phase Goal:** An LLM Supervisor (Claude) governs all corpus expansion decisions -- approving/rejecting thinkers, sources, and candidates with a full audit trail, graceful degradation when the Anthropic API is unavailable, and scheduled health checks and digests
**Verified:** 2026-03-09T06:00:00Z
**Status:** passed
**Re-verification:** No -- initial verification

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | A new thinker submitted for approval enters awaiting_llm status, the LLM Supervisor reviews it with a bounded context snapshot, and the decision is logged in llm_reviews with full prompt, response, and reasoning | VERIFIED | `handle_llm_approval_check` in `src/thinktank/handlers/llm_approval_check.py` creates LLMReview row (lines 131-145) with context_snapshot, prompt_used, llm_response, decision, decision_reasoning, model, tokens_used, duration_ms. Integration test `test_audit_trail_completeness` (line 236) asserts all fields non-null. |
| 2 | Source approval and candidate promotion follow the same gated flow -- workers never process unapproved sources or promote unapproved candidates | VERIFIED | Handler dispatches source_approval and candidate_review via `_REVIEW_TYPE_CONFIG` (lines 47-63). Integration tests `test_source_approval_approved` and `test_candidate_promotion_creates_thinker` confirm. Contract tests verify side effects for all 3 review types. |
| 3 | When the Anthropic API is unavailable, jobs awaiting LLM review are automatically escalated to human review after llm_timeout_hours, and the existing pipeline continues | VERIFIED | `escalate_timed_out_reviews` in `escalation.py` (lines 19-76) updates jobs via jsonb_set with needs_human_review flag, creates LLMReview with decision="escalate_to_human". Integration tests verify: `test_escalation_flags_timed_out_job` (timed-out job gets flag), `test_escalation_skips_recent_job` (recent job untouched), `test_escalation_skips_already_flagged`. API error in handler propagates for worker retry (`test_api_unavailable_raises`). Scheduled tasks catch Exception broadly and return None (`test_scheduled_task_handles_api_error`). |
| 4 | Scheduled health checks run every 6 hours, daily digests run at 07:00 UTC, and weekly audits run on Mondays -- all producing structured summaries logged to llm_reviews | VERIFIED | Worker loop creates 4 scheduler tasks (lines 111-122): escalation_task (900s), health_check_task (21600s=6h), digest_task (uses `seconds_until_next_utc_hour(7)`), audit_task (uses `seconds_until_next_monday_utc(7)`). Scheduled implementations in `scheduled.py` create LLMReview rows. Integration tests confirm: `test_health_check_creates_review`, `test_daily_digest_creates_review`, `test_weekly_audit_creates_review`. |
| 5 | Context snapshots are bounded (max 50 thinkers, 100 errors, 20 candidates per review) and tokens_used is tracked per review | VERIFIED | `snapshots.py` uses explicit `.limit()` calls: `.limit(20)` on candidates (lines 171, 181), `.limit(100)` on error log (line 239), `.limit(50)` on source health (line 259) and inactive thinkers (line 443). `tokens_used` tracked via `response.usage.input_tokens + response.usage.output_tokens` in `client.py` (line 83), stored in every LLMReview row. Unit test `test_limits_candidates_to_20` verifies bound. |

**Score:** 5/5 truths verified

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `src/thinktank/llm/__init__.py` | Package init | VERIFIED | Exists as empty package marker |
| `src/thinktank/llm/client.py` | AsyncAnthropic wrapper with review() | VERIFIED | 86 lines. LLMClient class with tool_use structured output pattern, returns (parsed_response, tokens_used, duration_ms). Model = "claude-sonnet-4-20250514", max_retries=2. |
| `src/thinktank/llm/schemas.py` | 6 Pydantic response models | VERIFIED | 69 lines. ThinkerApprovalResponse, SourceApprovalResponse, CandidateReviewResponse, HealthCheckResponse, DailyDigestResponse, WeeklyAuditResponse. All use Literal types for decision field validation. |
| `src/thinktank/llm/prompts.py` | SYSTEM_PROMPT + 6 prompt builders | VERIFIED | 198 lines. SYSTEM_PROMPT constant + build_{thinker_approval,source_approval,candidate_review,health_check,daily_digest,weekly_audit}_prompt. Each returns (system_prompt, user_prompt) with JSON-serialized context. |
| `src/thinktank/llm/snapshots.py` | 6 bounded context builders | VERIFIED | 484 lines. All 6 snapshot builders with explicit .limit() calls. Uses selectinload for async-safe relationship loading. |
| `src/thinktank/llm/decisions.py` | Decision dispatch + candidate promotion | VERIFIED | 233 lines. apply_decision dispatcher, apply_thinker_decision, apply_source_decision, apply_candidate_decision, promote_candidate_to_thinker. Correctly updates approval_status, links llm_review_id, creates Thinker from candidate. |
| `src/thinktank/llm/time_utils.py` | Schedule computation | VERIFIED | 66 lines. seconds_until_next_utc_hour and seconds_until_next_monday_utc with _utc_now() for testability. |
| `src/thinktank/llm/escalation.py` | Timeout escalation | VERIFIED | 77 lines. escalate_timed_out_reviews uses raw SQL with jsonb_set, creates LLMReview rows, reads llm_timeout_hours from config. |
| `src/thinktank/llm/scheduled.py` | Health check, digest, audit | VERIFIED | 201 lines. run_health_check, run_daily_digest, run_weekly_audit. Module-level _llm_client singleton. Each creates LLMReview row. All wrapped in try/except for graceful degradation. |
| `src/thinktank/handlers/llm_approval_check.py` | Approval handler | VERIFIED | 169 lines. handle_llm_approval_check with REVIEW_TYPE_CONFIG dispatch, LLMReview audit trail creation, apply_decision call. Dynamic function resolution for testability. |
| `src/thinktank/handlers/registry.py` | Handler registration | VERIFIED | 60 lines. Line 59: `register_handler("llm_approval_check", handle_llm_approval_check)` in Phase 5 section. |
| `src/thinktank/queue/errors.py` | Extended error categorization | VERIFIED | 73 lines. anthropic.RateLimitError -> LLM_API_ERROR, anthropic.APIConnectionError/APITimeoutError -> LLM_TIMEOUT, anthropic.APIStatusError -> LLM_API_ERROR, pydantic.ValidationError -> LLM_PARSE_ERROR. Checked before generic Python exceptions. |
| `src/thinktank/worker/loop.py` | 4 new LLM schedulers | VERIFIED | 511 lines. _llm_timeout_escalation_scheduler (900s), _llm_health_check_scheduler (21600s), _llm_digest_scheduler (recompute-on-iteration), _llm_audit_scheduler (recompute-on-iteration). All cancelled on shutdown (lines 219-225). |
| `tests/unit/test_llm_client.py` | Client unit tests | VERIFIED | 140 lines. 9 tests covering API call params, tool_use pattern, response parsing, token tracking, exception propagation. |
| `tests/unit/test_llm_schemas.py` | Schema validation tests | VERIFIED | 205 lines. 23 tests covering all 6 schemas with valid/invalid inputs and Literal enforcement. |
| `tests/unit/test_llm_prompts.py` | Prompt builder tests | VERIFIED | 117 lines. 14 tests verifying (system, user) prompt pairs for all review types. |
| `tests/unit/test_llm_snapshots.py` | Snapshot builder tests | VERIFIED | 243 lines. 10 tests verifying dict shapes, bounds, timezone-naive datetimes. |
| `tests/unit/test_llm_decisions.py` | Decision logic tests | VERIFIED | 340 lines. 21 tests covering all decision outcomes, promotion, pending job updates. |
| `tests/unit/test_llm_approval_handler.py` | Handler unit tests | VERIFIED | 332 lines. 12 tests covering dispatch, audit trail, apply_decision args, error cases. |
| `tests/unit/test_llm_time_utils.py` | Time utility tests | VERIFIED | 131 lines. 13 tests with frozen time for both functions. |
| `tests/unit/test_llm_escalation.py` | Escalation signature tests | VERIFIED | 27 lines. 3 tests verifying function signature. Full DB tests in integration. |
| `tests/unit/test_errors.py` | Error categorization tests | VERIFIED | 156 lines. 15 tests including 8 new anthropic/pydantic error tests + 7 existing. |
| `tests/integration/test_llm_approval.py` | Approval flow integration tests | VERIFIED | 353 lines. 8 tests covering thinker approved/rejected/escalated, source approved, candidate promotion, audit trail completeness, pending job linking, API unavailability. |
| `tests/integration/test_llm_escalation.py` | Escalation integration tests | VERIFIED | 165 lines. 4 tests against real PostgreSQL: flags timed-out job, skips recent, skips already flagged, returns count. |
| `tests/integration/test_llm_scheduled.py` | Scheduled task integration tests | VERIFIED | 158 lines. 5 tests: health check, daily digest, weekly audit create reviews, handles API error gracefully, config adjustments stored. |
| `tests/contract/test_llm_approval_handler.py` | Contract tests | VERIFIED | 166 lines. 3 contract tests verifying handler side effects (1 LLMReview + entity update per review type). |

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `client.py` | `anthropic.AsyncAnthropic` | `messages.create()` with tool_use | WIRED | Line 67: `await self._client.messages.create()` with tools param and tool_choice forcing structured_output |
| `snapshots.py` | `models/` | Bounded SQLAlchemy queries with `.limit()` | WIRED | `.limit(20)` at lines 171, 181; `.limit(100)` at line 239; `.limit(50)` at lines 259, 443; `.limit(10)` at line 125; `.limit(5)` at line 363 |
| `decisions.py` | `models/thinker.py` | approval_status update and Thinker creation | WIRED | `thinker.approval_status = "approved"` at line 109; `Thinker(...)` creation at line 214; `candidate.thinker_id = thinker.id` at line 229 |
| `llm_approval_check.py` | `client.py` | `LLMClient.review()` call | WIRED | Line 126: `await _llm_client.review(system_prompt, user_prompt, response_schema)` |
| `llm_approval_check.py` | `snapshots.py` | `build_*_context()` calls | WIRED | Lines 118, 120: `await snapshot_builder(session, ...)` via dynamic dispatch |
| `llm_approval_check.py` | `decisions.py` | `apply_decision()` call | WIRED | Line 154: `await apply_decision(session, review_type, target_id, pending_job_id, result, review.id)` |
| `llm_approval_check.py` | `review.py` | `LLMReview()` row creation | WIRED | Lines 131-145: `LLMReview(id=..., review_type=..., trigger="job_gate", ...)` |
| `registry.py` | `llm_approval_check.py` | Handler import + registration | WIRED | Line 9: import, Line 59: `register_handler("llm_approval_check", handle_llm_approval_check)` |
| `loop.py` | `escalation.py` | `escalate_timed_out_reviews()` in scheduler | WIRED | Line 25: import, Line 404: `count = await escalate_timed_out_reviews(session)` |
| `loop.py` | `scheduled.py` | `run_health_check/run_daily_digest/run_weekly_audit` in schedulers | WIRED | Line 26: imports, Lines 432, 461, 490: scheduler calls |
| `scheduled.py` | `client.py` | `LLMClient.review()` | WIRED | Line 33: `_llm_client = LLMClient()`, Lines 53, 109, 158: `await _llm_client.review(...)` |
| `scheduled.py` | `snapshots.py` | `build_*_context()` | WIRED | Lines 23-27: imports, Lines 50, 106, 155: `await build_*_context(session)` |
| `escalation.py` | `models/job.py` | UPDATE jobs with needs_human_review | WIRED | Lines 35-46: raw SQL `UPDATE jobs SET payload = jsonb_set(...)` with `needs_human_review` |

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|------------|-------------|--------|----------|
| GOV-01 | 05-01 | LLM Supervisor using Claude claude-sonnet-4-20250514 with structured JSON prompts | SATISFIED | `client.py` line 29: `self.model = "claude-sonnet-4-20250514"`, tool_use structured output pattern. 6 prompt builders produce structured JSON context. |
| GOV-02 | 05-02 | Thinker approval flow with context snapshot before activation | SATISFIED | Handler dispatches `thinker_approval`, snapshot builds bounded context, decision updates approval_status. Integration test `test_thinker_approval_approved` confirms full flow. |
| GOV-03 | 05-02 | Source approval flow before RSS polling begins | SATISFIED | Handler dispatches `source_approval`, decision updates source.approval_status. Integration test `test_source_approval_approved` confirms. Source with approval_status != "approved" won't be polled (Phase 3 handler logic). |
| GOV-04 | 05-02 | Candidate thinker batch review exceeding appearance threshold | SATISFIED | Handler dispatches `candidate_review`, snapshot queries `CandidateThinker.status == "pending_llm"` ordered by `appearance_count.desc()` with `.limit(20)`. Integration test `test_candidate_promotion_creates_thinker` confirms. |
| GOV-05 | 05-01, 05-02 | Full audit trail in llm_reviews table | SATISFIED | Every LLM call creates LLMReview with context_snapshot, prompt_used, llm_response, decision, decision_reasoning, model, tokens_used, duration_ms. Integration test `test_audit_trail_completeness` asserts all fields non-null. |
| GOV-06 | 05-03 | Fallback/timeout escalation after llm_timeout_hours | SATISFIED | `escalation.py` updates jobs past timeout with needs_human_review flag, creates escalation LLMReview. Scheduler runs every 15 min. Integration tests confirm timeout logic and skip behavior. |
| GOV-07 | 05-02, 05-03 | Graceful degradation when Anthropic API unavailable | SATISFIED | Anthropic exceptions categorized (LLM_API_ERROR, LLM_TIMEOUT, LLM_PARSE_ERROR) for worker retry. Scheduled tasks catch Exception broadly and return None. `test_api_unavailable_raises` confirms propagation. `test_scheduled_task_handles_api_error` confirms graceful handling. |
| GOV-08 | 05-03 | Scheduled health checks (6h), daily digests (07:00 UTC), weekly audits (Mondays) | SATISFIED | Worker loop: health_check at 21600s interval, digest via `seconds_until_next_utc_hour(7)`, audit via `seconds_until_next_monday_utc(7)`. Integration tests confirm LLMReview row creation for all 3. |
| GOV-09 | 05-01, 05-03 | Context budgeting with bounded snapshots (max 50 thinkers, 100 errors, 20 candidates) | SATISFIED | Explicit `.limit()` calls in `snapshots.py` matching spec bounds. `tokens_used` tracked on every review via `response.usage`. Unit test verifies 20-candidate limit. |
| DISC-06 | 05-01 | Candidate-to-thinker promotion flow triggered by LLM batch review | SATISFIED | `promote_candidate_to_thinker` in `decisions.py` creates Thinker row from candidate data, sets candidate.thinker_id, candidate.status="promoted". Integration test `test_candidate_promotion_creates_thinker` confirms end-to-end. |

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| (none) | - | - | - | No anti-patterns found. All source files are free of TODO/FIXME/placeholder comments, empty implementations, and stub handlers. |

### Human Verification Required

### 1. LLM Tool-Use Response Parsing

**Test:** Run the application with a real ANTHROPIC_API_KEY and trigger a thinker approval flow. Verify the Anthropic API returns a tool_use block that parses correctly into the Pydantic model.
**Expected:** LLMReview row is created with valid decision, reasoning, and all audit fields populated.
**Why human:** Mocked tests cannot verify real Anthropic API response format. SDK version compatibility with tool_use pattern needs runtime validation.

### 2. Scheduler Timing Accuracy

**Test:** Start the worker loop and observe scheduler behavior over a few hours. Verify health check fires approximately every 6 hours and daily digest waits until 07:00 UTC.
**Expected:** Schedulers fire at expected intervals, no drift accumulation over multiple iterations.
**Why human:** Timing behavior over hours cannot be verified in unit/integration tests that use frozen time.

### 3. Escalation Under Real Load

**Test:** Simulate an unavailable Anthropic API for 3+ hours with pending awaiting_llm jobs. Verify the escalation scheduler flags them with needs_human_review.
**Expected:** Jobs older than llm_timeout_hours get flagged. Jobs younger than the timeout remain untouched. LLMReview escalation rows are created.
**Why human:** Integration tests verify the logic with manipulated timestamps, but real-time behavior under sustained API outage needs runtime validation.

### Gaps Summary

No gaps found. All 5 observable truths verified. All 26 artifacts exist, are substantive, and are correctly wired. All 11 requirements (GOV-01 through GOV-09, DISC-06) are satisfied with implementation evidence. No anti-patterns detected in any source file. Git history shows 10 commits across all 3 plans with proper atomic task commits.

The phase delivers a complete LLM governance system with:
- Event-driven approval pipeline (thinker, source, candidate via llm_approval_check handler)
- Clock-driven health monitoring (health check every 6h, daily digest at 07:00 UTC, weekly audit on Mondays)
- Timeout escalation to human review (every 15 min check for jobs past llm_timeout_hours)
- Full audit trail (every decision logged to llm_reviews with context, prompt, response, reasoning, tokens, duration)
- Graceful degradation (scheduled tasks catch exceptions, approval failures retry via worker queue)
- Bounded context budgets (50 thinkers, 100 errors, 20 candidates per review)
- 154 new tests across 13 test files (77 in Plan 01 + 52 in Plan 02 + 25 in Plan 03)

---

_Verified: 2026-03-09T06:00:00Z_
_Verifier: Claude (gsd-verifier)_
