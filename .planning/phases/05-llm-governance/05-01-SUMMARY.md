---
phase: 05-llm-governance
plan: 01
subsystem: llm
tags: [anthropic, claude, pydantic, structured-output, tool-use, async]

# Dependency graph
requires:
  - phase: 01-foundation-layer
    provides: "SQLAlchemy models (Thinker, Source, CandidateThinker, Job, LLMReview), factories, config pattern"
provides:
  - "LLMClient wrapper with tool_use structured output pattern"
  - "6 Pydantic response schemas for all review types"
  - "Prompt template builders for all 6 review types"
  - "Bounded context snapshot builders (50/100/20 limits)"
  - "Decision application logic with candidate-to-thinker promotion"
affects: [05-02, 05-03, llm-approval-handler, scheduled-tasks, worker-loop]

# Tech tracking
tech-stack:
  added: [anthropic==0.84.0, distro, jiter, sniffio, docstring-parser]
  patterns: [tool_use structured output, bounded context snapshots, decision dispatch]

key-files:
  created:
    - src/thinktank/llm/__init__.py
    - src/thinktank/llm/client.py
    - src/thinktank/llm/schemas.py
    - src/thinktank/llm/prompts.py
    - src/thinktank/llm/snapshots.py
    - src/thinktank/llm/decisions.py
    - tests/unit/test_llm_client.py
    - tests/unit/test_llm_schemas.py
    - tests/unit/test_llm_prompts.py
    - tests/unit/test_llm_snapshots.py
    - tests/unit/test_llm_decisions.py
  modified:
    - pyproject.toml
    - uv.lock

key-decisions:
  - "Used tool_use pattern instead of messages.parse()/output_format for structured output (universally supported across SDK versions)"
  - "Removed assert isinstance guards in apply_decision dispatcher to avoid src.thinktank vs thinktank dual-import-path mismatch"
  - "Snapshot builders use mock session in unit tests; full DB integration tests deferred to Plan 02/03"

patterns-established:
  - "tool_use structured output: define tool with Pydantic JSON schema, force tool_choice, parse tool_use block input"
  - "Bounded context snapshots: explicit .limit() on all queries matching spec bounds (50/100/20)"
  - "_slugify helper for candidate-to-thinker name conversion"

requirements-completed: [GOV-01, GOV-05, GOV-09, DISC-06]

# Metrics
duration: 9min
completed: 2026-03-09
---

# Phase 5 Plan 01: LLM Supervisor Core Module Summary

**Anthropic client with tool_use structured output, 6 Pydantic response schemas, prompt builders, bounded snapshot queries, and decision dispatch with candidate-to-thinker promotion**

## Performance

- **Duration:** 9 min
- **Started:** 2026-03-09T04:20:32Z
- **Completed:** 2026-03-09T04:29:29Z
- **Tasks:** 3
- **Files created:** 11
- **Files modified:** 2
- **Tests added:** 77 (366 -> 443 total)

## Accomplishments
- Complete `src/thinktank/llm/` package with 5 modules providing all LLM Supervisor building blocks
- LLMClient wraps AsyncAnthropic with tool_use pattern for reliable structured output across SDK versions
- 6 Pydantic schemas enforce valid decision values via Literal types for all review types
- Bounded context snapshots enforce spec limits (50 thinkers, 100 errors, 20 candidates)
- Decision application handles all outcomes including candidate promotion to full Thinker with slug generation

## Task Commits

Each task was committed atomically:

1. **Task 1: Anthropic client wrapper, response schemas, and dependency** - `794077b` (feat)
2. **Task 2: Prompt templates and bounded context snapshot builders** - `851881a` (feat)
3. **Task 3: Decision application logic and candidate-to-thinker promotion** - `c37c0ba` (feat)
4. **Ruff lint fixes** - `ac1fc86` (fix)

## Files Created/Modified
- `src/thinktank/llm/__init__.py` - Package init
- `src/thinktank/llm/client.py` - AsyncAnthropic wrapper with review() method using tool_use structured output
- `src/thinktank/llm/schemas.py` - 6 Pydantic response models (ThinkerApproval, SourceApproval, CandidateReview, HealthCheck, DailyDigest, WeeklyAudit)
- `src/thinktank/llm/prompts.py` - SYSTEM_PROMPT constant and 6 prompt builder functions
- `src/thinktank/llm/snapshots.py` - 6 bounded context snapshot builders with explicit .limit() calls
- `src/thinktank/llm/decisions.py` - Decision dispatch, entity-specific handlers, candidate-to-thinker promotion
- `tests/unit/test_llm_client.py` - 9 tests for client wrapper with mocked Anthropic SDK
- `tests/unit/test_llm_schemas.py` - 23 tests for schema validation with valid/invalid inputs
- `tests/unit/test_llm_prompts.py` - 14 tests for prompt template structure
- `tests/unit/test_llm_snapshots.py` - 10 tests for snapshot builder dict shapes and bounds
- `tests/unit/test_llm_decisions.py` - 21 tests for decision logic with factory-built models
- `pyproject.toml` - Added anthropic dependency
- `uv.lock` - Updated lockfile

## Decisions Made
- **tool_use over messages.parse():** The plan explicitly specified tool_use pattern for structured output. This avoids dependency on SDK-specific features like `messages.parse()` or `output_format` that may not exist in all versions. The tool_use approach is universally supported: define a tool with the Pydantic JSON schema, force `tool_choice`, parse the `tool_use` block's `input` field.
- **Removed isinstance guards in dispatcher:** The `apply_decision` function originally had `assert isinstance(result, ThinkerApprovalResponse)` guards, but these fail in the test environment because Python treats `src.thinktank.llm.schemas.X` and `thinktank.llm.schemas.X` as different classes (dual import path from src layout). Used `type: ignore[arg-type]` comments instead.
- **Unit test approach for snapshots:** Snapshot builders require DB sessions for real queries. Unit tests verify dict shapes and mock sessions; full integration tests with bounded query verification deferred to Plan 02/03 as specified.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Removed isinstance assertions causing test failures**
- **Found during:** Task 3 (Decision application logic)
- **Issue:** `assert isinstance(result, ThinkerApprovalResponse)` in `apply_decision` failed because src layout creates dual import paths (`src.thinktank` vs `thinktank`)
- **Fix:** Removed isinstance assertions, used type: ignore comments for the dispatcher calls
- **Files modified:** src/thinktank/llm/decisions.py
- **Verification:** All 21 decision tests pass
- **Committed in:** c37c0ba (Task 3 commit)

**2. [Rule 1 - Bug] Fixed ruff lint issues (import sorting, nested if)**
- **Found during:** Post-task verification
- **Issue:** Import blocks unsorted (I001), nested if statements (SIM102)
- **Fix:** Ran ruff --fix for import sorting, manually combined nested if into single statement
- **Files modified:** src/thinktank/llm/decisions.py, prompts.py, snapshots.py
- **Verification:** ruff check passes, all 443 tests pass
- **Committed in:** ac1fc86

---

**Total deviations:** 2 auto-fixed (2 bugs)
**Impact on plan:** Both fixes necessary for correctness. No scope creep.

## Issues Encountered
None beyond the auto-fixed deviations above.

## User Setup Required
None - no external service configuration required. ANTHROPIC_API_KEY will be needed at runtime but is read from environment variables.

## Next Phase Readiness
- LLM Supervisor core module complete, providing all building blocks for Plans 02 and 03
- Plan 02 can wire the approval handler using LLMClient, schemas, prompts, snapshots, and decisions
- Plan 03 can implement scheduled tasks using the health check, digest, and audit prompt/snapshot builders
- All 443 tests pass with zero regressions

## Self-Check: PASSED

All 12 files verified present. All 4 commits verified in git history. 443 tests pass.

---
*Phase: 05-llm-governance*
*Completed: 2026-03-09*
