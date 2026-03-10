---
phase: 12-agent-chat
plan: 01
subsystem: api
tags: [anthropic, sse, streaming, chat-agent, fastapi, tool-use]

# Dependency graph
requires:
  - phase: 11-pipeline-control
    provides: "All admin features available for agent to interact with"
provides:
  - "Agent chat backend: system prompt, tool definitions, session store, streaming, 3 API endpoints"
  - "query_database tool for read-only SQL against all ThinkTank tables"
  - "propose_action tool with 8 mutation action types"
  - "SSE streaming via sse-starlette EventSourceResponse"
affects: [12-02-PLAN (chat drawer UI consumes these endpoints)]

# Tech tracking
tech-stack:
  added: [sse-starlette]
  patterns: [EventSourceResponse for SSE streaming, dict-based event yielding, tool-use loop with max iterations, propose-then-execute mutation pattern]

key-files:
  created:
    - src/thinktank/agent/__init__.py
    - src/thinktank/agent/system_prompt.py
    - src/thinktank/agent/tools.py
    - src/thinktank/agent/session.py
    - src/thinktank/agent/stream.py
    - src/thinktank/admin/routers/chat.py
    - tests/integration/test_admin_chat_api.py
  modified:
    - src/thinktank/admin/main.py
    - pyproject.toml

key-decisions:
  - "stream.py yields dicts (not SSE strings) -- router JSON-serializes and EventSourceResponse wraps with data: prefix"
  - "async_session_factory() used directly in SSE endpoint (not Depends) since SSE outlives request lifecycle"
  - "In-memory session store (not DB-backed) -- sufficient for single-admin use case"

patterns-established:
  - "Agent tool pattern: AGENT_TOOLS list with Anthropic input_schema format, execute_tool dispatcher, execute_confirmed_action for mutations"
  - "SSE streaming pattern: async generator yields dicts, router serializes to JSON strings, EventSourceResponse handles SSE framing"
  - "Propose-then-execute: mutations never auto-execute, stored as pending proposals, require explicit /confirm call"

requirements-completed: [CHAT-02, CHAT-03, CHAT-04]

# Metrics
duration: 9min
completed: 2026-03-10
---

# Phase 12 Plan 01: Agent Chat Backend Summary

**LLM chat agent backend with read-only SQL queries, 8 mutation action types via propose-then-execute, SSE streaming via sse-starlette, and in-memory session management**

## Performance

- **Duration:** 9 min
- **Started:** 2026-03-10T06:54:13Z
- **Completed:** 2026-03-10T07:03:28Z
- **Tasks:** 2
- **Files modified:** 9

## Accomplishments
- Complete agent package (4 modules) with system prompt containing full database schema for all 11 tables
- Two tools: query_database (SELECT-only with LIMIT injection) and propose_action (8 action types)
- execute_confirmed_action implements add_thinker, approve/reject_source, trigger_discovery, toggle_kill_switch, update_config, retry_job, cancel_job -- all with proper DB mutations and audit trails
- Three API endpoints: POST /admin/chat/send (SSE), POST /admin/chat/confirm/{proposal_id} (JSON), GET /admin/chat/history (JSON)
- 13 integration tests all passing with mocked Anthropic API

## Task Commits

Each task was committed atomically:

1. **Task 1: Agent package with system prompt, tools, session store, and streaming** - `aebc497` (feat)
2. **Task 2: Chat router with SSE streaming, confirm, and history endpoints** - `0a92089` (feat)

## Files Created/Modified
- `src/thinktank/agent/__init__.py` - Empty package init
- `src/thinktank/agent/system_prompt.py` - build_chat_system_prompt() with schema summary and tool instructions
- `src/thinktank/agent/tools.py` - AGENT_TOOLS definitions, execute_tool, execute_confirmed_action (8 action types)
- `src/thinktank/agent/session.py` - ChatSessionStore with message history, pending proposals, Anthropic format conversion
- `src/thinktank/agent/stream.py` - stream_agent_response async generator with tool-use loop (max 5 iterations)
- `src/thinktank/admin/routers/chat.py` - 3 endpoints: /send (SSE), /confirm (JSON), /history (JSON)
- `src/thinktank/admin/main.py` - Added chat_router import and include_router
- `pyproject.toml` - Added sse-starlette>=2.0.0 dependency
- `tests/integration/test_admin_chat_api.py` - 13 integration tests covering all endpoints and tools

## Decisions Made
- stream.py yields dicts instead of pre-formatted SSE strings -- cleaner separation of concerns since EventSourceResponse handles SSE framing
- Used async_session_factory() directly in SSE endpoint (not FastAPI Depends) because SSE streams outlive the normal request-response lifecycle
- In-memory session store with module-level singleton -- appropriate for single-admin use case, avoids DB overhead for chat history

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed SSE double-wrapping**
- **Found during:** Task 2 (chat router testing)
- **Issue:** stream.py yielded `data: {...}\n\n` strings, but EventSourceResponse also added `data:` prefix, resulting in `data: data: {...}`
- **Fix:** Changed stream.py to yield plain dicts, router JSON-serializes them, EventSourceResponse handles SSE framing
- **Files modified:** src/thinktank/agent/stream.py, src/thinktank/admin/routers/chat.py
- **Verification:** SSE test parsing confirmed correct event format
- **Committed in:** 0a92089 (Task 2 commit)

**2. [Rule 1 - Bug] Fixed expire_all() async call**
- **Found during:** Task 2 (test_cancel_pending_job)
- **Issue:** `await session.expire_all()` but expire_all is synchronous in SQLAlchemy
- **Fix:** Used fresh session from session_factory for verification query after execute_confirmed_action commits
- **Files modified:** tests/integration/test_admin_chat_api.py
- **Committed in:** 0a92089 (Task 2 commit)

---

**Total deviations:** 2 auto-fixed (2 bugs)
**Impact on plan:** Both fixes necessary for correctness. No scope creep.

## Issues Encountered
None beyond the auto-fixed deviations above.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Chat backend fully operational: all 3 endpoints working, tools defined, streaming functional
- Plan 12-02 (Chat Drawer UI) can build on these endpoints directly
- SSE format verified: `data: {"type": "text_delta", "text": "..."}\n\n` events ready for frontend consumption

---
*Phase: 12-agent-chat*
*Completed: 2026-03-10*
