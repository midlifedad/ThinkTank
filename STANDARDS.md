# Engineering Standards

These standards govern all development on this project. Every phase of work inherits from these principles. They are not aspirational — they are constraints.

---

## Testing

### 1. Test pyramid with clear boundaries

- **Unit**: Pure logic, no I/O. Fast enough to run on every save.
- **Integration**: Real database, real file system, mocked external services. Transaction-per-test with rollback.
- **E2E**: Full system flow with all services running. Tagged slow, run in CI.

### 2. External dependencies are always mocked below E2E

- Checked-in fixture files for third-party API responses.
- No test should fail because an external service is down.
- One dedicated fixture directory, organized by service name.

### 3. Test data is generated, not static

- Factory functions that produce valid domain objects with sensible defaults.
- Every field overridable — tests specify only what they care about.
- Relational data (parent/child, junction tables) composed from factories, not hand-wired SQL.

### 4. Database tests use the real engine

- If the production database is Postgres, tests run against Postgres — not SQLite.
- Migrations run once per test session. Each test gets a transaction that rolls back.
- Docker Compose provides the test database. CI provisions it the same way.

### 5. Expensive resources are opt-in

- GPU, paid APIs, large model inference: tagged and skipped by default.
- The default test suite completes in under 60 seconds with zero external dependencies beyond a local database.

### 6. Every public interface has a contract test

- API endpoints: request/response shape, status codes, error formats.
- Job handlers: given input payload, expected side effects (rows created, jobs enqueued).
- Decision logic: given context, expected decision for each branch.

---

## Observability

### 1. Structured logging from line one

- JSON format. Every log line carries enough context to correlate without grepping.
- Standard fields on every entry: timestamp, service name, correlation ID, severity.
- Domain-specific fields added per-service (but consistent naming across services).

### 2. Log levels mean something

- `ERROR`: Something failed and needs human attention.
- `WARNING`: Something degraded but the system compensated (retry, fallback, throttle).
- `INFO`: Normal lifecycle events — a unit of work started, completed, or changed state.
- `DEBUG`: Internal details useful during development — raw payloads, intermediate calculations.

### 3. Correlation across units of work

- Every top-level operation gets an ID that propagates to all child operations.
- The operational data store (database, log aggregator) supports filtering by correlation ID.
- You should be able to answer "what happened as a result of X?" with a single query.

### 4. Health endpoints on every service

- `GET /health` returns 200 if the service can serve requests (DB connected, dependencies reachable).
- Long-running workers report heartbeats — a periodic signal that they're alive and processing.
- Stale heartbeats trigger alerts before users notice.

### 5. Metrics are derived from operational data, not bolted on

- If the system already records timestamps on state transitions, compute latency from those — don't add a separate metrics layer.
- Counters and gauges come from queries against the primary data store until scale demands a dedicated metrics system.
- Cost tracking is a first-class metric, not an afterthought.

### 6. The system tells you when it's unhealthy before you ask

- Thresholds defined upfront for error rates, queue depth, latency.
- Breaches surface automatically (dashboard banner, log escalation, notification).
- "I didn't know it was broken" is a missing alert, not an acceptable state.

---

## Documentation

### 1. Four documents, four audiences

| Document | Audience | Answers |
|----------|----------|---------|
| **README** | New developer | What is this? How do I run it locally in 5 minutes? |
| **Architecture** | Developer building features | How does the system work? Where does this feature fit? |
| **Development Guide** | Developer writing code | How do I add a new X? What are the conventions? How do I test? |
| **Operations Runbook** | Person deploying/operating | How do I bootstrap? What do I check after deploy? How do I fix common problems? |

### 2. Docs live next to what they describe

- System-level docs in `docs/`.
- API docs auto-generated from code (OpenAPI, docstrings).
- Decision records in `docs/adr/` — one per decision, numbered, immutable once written.

### 3. If it's not in the runbook, it's not operationally ready

- Every deployable feature must update the operations runbook.
- Bootstrap sequence, post-deploy verification, rollback procedure — all documented before the feature ships.

### 4. Architecture docs are living documents

- Updated when the system changes, not after.
- Data flow diagrams, service boundaries, and state machines are visual, not just prose.

---

## Deployment

### 1. Local dev mirrors production topology, not tools

- Same database engine, same service boundaries, same migration path.
- Docker Compose for infrastructure dependencies. Application code runs natively for fast iteration.
- No feature should require the cloud provider to develop or test.

### 2. Migrations are forward-only and reversible

- Every schema change is a migration — never raw DDL against a database with data.
- Every migration has a tested rollback path.
- Migrations run automatically on deploy. Manual migration steps are a deployment bug.

### 3. Deploy is a single action with a verification step

- Push triggers deploy. Deploy runs migrations. Migrations complete before traffic routes.
- Post-deploy verification checklist is codified, not tribal knowledge.
- Rollback is a single action too — not a series of manual steps.

### 4. Secrets never touch code

- Environment variables for all credentials. No defaults that look like real keys.
- `.env.example` with placeholder values checked in. `.env` is gitignored.
- Secret rotation doesn't require a code change or redeploy.

### 5. Seed data is idempotent

- Running seeds twice produces the same result as running them once.
- Seeds use upsert semantics (`ON CONFLICT DO UPDATE` or equivalent).
- Seed scripts are version-tracked and part of the bootstrap sequence.

---

## Code Conventions

### 1. One formatter, one linter, zero debates

- Auto-format on save. CI rejects unformatted code.
- Linter rules are checked in and enforced — not advisory.
- Type hints on all public interfaces. Type checker runs in CI.

### 2. Errors are categorized, not just caught

- Every error that crosses a system boundary gets a category string (not just a stack trace).
- Categories are a closed set, defined upfront, extended deliberately.
- No bare `except`. No swallowed exceptions. Every catch either handles, categorizes, or re-raises.

### 3. Configuration has clear precedence

- Environment variables > database config > code defaults.
- Every configurable value has a sensible default that works for local development.
- Feature toggles live in the database so they can be changed without redeploying.

### 4. Dependencies are locked

- Lock file checked in. `install` is deterministic.
- One dependency manager, declared in the README.
