---
phase: 05-llm-governance
plan: 02
subsystem: llm-handlers
tags: [handler, error-categorization, integration-tests, contract-tests, approval-pipeline]

# Dependency graph
requires:
  - phase: 05-llm-governance
    plan: 01
    provides: "LLMClient, Pydantic response schemas, prompt builders, snapshot builders, decision application"
provides:
  - "llm_approval_check handler registered in handler registry"
  - "Anthropic SDK error categorization in categorize_error()"
  - "Integration tests for thinker/source/candidate approval flows"
  - "Contract tests for handler side effects"
affects: [05-03, worker-loop, scheduled-tasks]

# Tech tracking
tech-stack:
  added: []
  patterns: [dynamic-function-resolution, selectinload-for-async, call-site-mocking]

key-files:
  created:
    - src/thinktank/handlers/llm_approval_check.py
    - tests/unit/test_llm_approval_handler.py
    - tests/integration/test_llm_approval.py
    - tests/contract/test_llm_approval_handler.py
  modified:
    - src/thinktank/handlers/registry.py
    - src/thinktank/queue/errors.py
    - src/thinktank/llm/snapshots.py
    - tests/unit/test_errors.py
    - tests/unit/test_llm_snapshots.py

key-decisions:
  - "Dynamic function resolution via sys.modules for patchable dispatch map (avoids stale import-time references in REVIEW_TYPE_MAP)"
  - "selectinload for snapshot builders to fix async lazy-loading in identity-map scenarios"
  - "noqa F401 on prompt/snapshot imports that are resolved dynamically at call time"

patterns-established:
  - "Dynamic dispatch with _resolve_func: import functions at module level for patching, resolve by name string at call time"
  - "selectinload on session.execute queries for async-safe relationship loading (replaces session.get for models with relationships)"

requirements-completed: [GOV-02, GOV-03, GOV-04, GOV-05, GOV-07]

# Metrics
duration: 9min
completed: 2026-03-09
---

# Phase 5 Plan 02: LLM Approval Handler and Integration Tests Summary

**llm_approval_check handler orchestrating thinker/source/candidate LLM-gated approval with full audit trail, anthropic error categorization, and integration/contract test coverage**

## Performance

- **Duration:** 9 min
- **Started:** 2026-03-09T04:33:01Z
- **Completed:** 2026-03-09T04:42:41Z
- **Tasks:** 2
- **Files created:** 4
- **Files modified:** 5
- **Tests added:** 52 (443 -> 495 total)

## Accomplishments
- `handle_llm_approval_check` handler dispatching thinker, source, and candidate reviews through the full LLM pipeline
- Handler creates complete LLMReview audit trail rows with context_snapshot, prompt_used, llm_response, decision, reasoning, model, tokens_used, duration_ms
- Extended `categorize_error()` with anthropic SDK exception handling (RateLimitError, APIConnectionError, APITimeoutError, APIStatusError, pydantic.ValidationError)
- Handler registered in registry as `llm_approval_check` (Phase 5 section)
- 8 integration tests covering approved/rejected/escalated flows, source approval, candidate promotion, audit trail completeness, pending job linking, API unavailability
- 3 contract tests verifying handler side effects (1 LLMReview + entity update per review type)

## Task Commits

Each task was committed atomically:

1. **Task 1: llm_approval_check handler, error categorization, registry** - `436b40d` (feat)
2. **Task 2: Integration and contract tests for LLM approval flows** - `0fab2d9` (feat)

## Files Created/Modified
- `src/thinktank/handlers/llm_approval_check.py` - Handler with REVIEW_TYPE_CONFIG dispatch, dynamic function resolution, LLMReview creation, decision application
- `src/thinktank/handlers/registry.py` - Added llm_approval_check handler registration (Phase 5 section)
- `src/thinktank/queue/errors.py` - Extended categorize_error with anthropic/pydantic isinstance checks before generic Python exceptions
- `src/thinktank/llm/snapshots.py` - Fixed build_thinker_approval_context and build_source_approval_context to use selectinload for async-safe relationship loading
- `tests/unit/test_llm_approval_handler.py` - 12 unit tests for handler dispatch, audit trail, apply_decision args, error cases
- `tests/unit/test_errors.py` - 8 new tests for anthropic/pydantic error categorization
- `tests/unit/test_llm_snapshots.py` - Updated unit tests for snapshot builder query refactor (session.execute instead of session.get)
- `tests/integration/test_llm_approval.py` - 8 integration tests for full approval flows with mocked LLM
- `tests/contract/test_llm_approval_handler.py` - 3 contract tests verifying handler side effects

## Decisions Made
- **Dynamic function resolution:** The REVIEW_TYPE_MAP originally stored direct function references, but these are captured at import time and can't be patched by tests. Switched to storing function name strings and resolving via `sys.modules[__name__]` at call time, allowing standard `unittest.mock.patch` to work on module-level names.
- **selectinload for snapshot queries:** `session.get()` from the identity map doesn't trigger selectin loading for relationships. When the factory creates a thinker and the handler then gets it via `session.get()`, the thinker's `sources` and `categories` relationships aren't loaded, causing `MissingGreenlet` errors in async context. Switched to `select().options(selectinload(...))` for reliable relationship loading.
- **noqa F401 for dynamic imports:** The prompt builder and snapshot builder functions are imported at module level (so tests can patch them) but referenced only by name string in `_REVIEW_TYPE_CONFIG`. Added `# noqa: F401` comments to suppress unused-import warnings.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed async lazy-loading in snapshot builders**
- **Found during:** Task 2 (Integration tests)
- **Issue:** `build_thinker_approval_context` and `build_source_approval_context` used `session.get()` which returns objects from the identity map without loading relationships. Accessing `thinker.sources` then triggered sync lazy loading, causing `MissingGreenlet` error in async context.
- **Fix:** Replaced `session.get()` with `select().options(selectinload(...))` for both thinker and source snapshot builders.
- **Files modified:** `src/thinktank/llm/snapshots.py`, `tests/unit/test_llm_snapshots.py`
- **Verification:** All 495 tests pass, including existing snapshot unit tests
- **Committed in:** 0fab2d9 (Task 2 commit)

**2. [Rule 1 - Bug] Fixed REVIEW_TYPE_MAP dispatch for test patchability**
- **Found during:** Task 1 (Unit tests)
- **Issue:** Dict stored direct function references captured at import time. `unittest.mock.patch` on module-level names didn't affect the dict values, so tests couldn't mock snapshot/prompt builders.
- **Fix:** Changed to storing function name strings, resolving via `_resolve_func()` at call time using `sys.modules[__name__]`
- **Files modified:** `src/thinktank/handlers/llm_approval_check.py`
- **Verification:** All handler unit tests can now patch snapshot/prompt builders
- **Committed in:** 436b40d (Task 1 commit)

---

**Total deviations:** 2 auto-fixed (2 bugs)
**Impact on plan:** Both fixes necessary for correctness. No scope creep.

## Issues Encountered
None beyond the auto-fixed deviations above.

## User Setup Required
None - ANTHROPIC_API_KEY will be needed at runtime but is read from environment variables.

## Next Phase Readiness
- LLM approval handler complete and registered, providing the event-driven approval track
- Plan 03 can implement scheduled tasks (health check, daily digest, weekly audit) using the same LLM pipeline
- All 495 tests pass with zero regressions

## Self-Check: PASSED

All 9 files verified present. Both commits verified in git history. 495 tests pass.

---
*Phase: 05-llm-governance*
*Completed: 2026-03-09*
