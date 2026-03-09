# ThinkTank Operations Runbook

This runbook covers day-to-day operations, deployment procedures, and troubleshooting for the ThinkTank ingestion platform.

## 1. Bootstrap (Fresh Deployment)

### Prerequisites

Before running bootstrap, ensure:

1. **PostgreSQL is running** and accessible
2. **`DATABASE_URL` environment variable** is set:
   ```
   DATABASE_URL=postgresql+asyncpg://thinktank:thinktank@localhost:5432/thinktank
   ```
3. **Schema migrations are applied:**
   ```bash
   uv run alembic upgrade head
   ```
4. **API keys are configured** (optional but recommended):
   - `ANTHROPIC_API_KEY` -- for LLM governance (Claude claude-sonnet-4-20250514)
   - `RAILWAY_API_KEY` -- for GPU worker scaling (if using Railway)

### Running Bootstrap

```bash
uv run python -m scripts.bootstrap
```

### Expected Output

```
Bootstrap complete: {'categories': 15, 'config': 10, 'thinkers': 5}
  Categories: 15
  Config entries: 10
  Thinkers: 5
  Workers: ACTIVE
```

### What Bootstrap Does

The bootstrap orchestrator (`scripts/bootstrap.py`) runs these steps in order:

1. **Validates schema** -- checks that the `categories` table exists
2. **Seeds categories** (`scripts/seed_categories.py`) -- creates 4 top-level categories with 11 subcategories using deterministic UUIDs
3. **Seeds config** (`scripts/seed_config.py`) -- inserts 10 operational defaults into `system_config` (workers start inactive)
4. **Validates categories exist** -- ensures categories were created before seeding thinkers
5. **Seeds thinkers** (`scripts/seed_thinkers.py`) -- creates 5 initial thinkers with `approval_status="pending_llm"` and enqueues `llm_approval_check` jobs for each
6. **Activates workers** -- sets `workers_active=true` in `system_config`

### Verification After Bootstrap

```bash
# Check categories exist
uv run python -c "
import asyncio
from sqlalchemy import select, func
from src.thinktank.database import async_session_factory
from src.thinktank.models.category import Category

async def check():
    async with async_session_factory() as s:
        result = await s.execute(select(func.count()).select_from(Category))
        print(f'Categories: {result.scalar()}')
asyncio.run(check())
"

# Check workers are active
uv run python -c "
import asyncio
from sqlalchemy import select
from src.thinktank.database import async_session_factory
from src.thinktank.models.config_table import SystemConfig

async def check():
    async with async_session_factory() as s:
        result = await s.execute(select(SystemConfig).where(SystemConfig.key == 'workers_active'))
        print(f'workers_active: {result.scalar_one().value}')
asyncio.run(check())
"
```

### Re-Running Individual Seeds

All seed scripts are idempotent. Re-running them is safe and will not create duplicates:

```bash
uv run python -m scripts.seed_categories   # Re-seed category taxonomy
uv run python -m scripts.seed_config       # Re-seed config defaults
uv run python -m scripts.seed_thinkers     # Re-seed initial thinkers
```

## 2. Post-Deploy Verification

After any deployment, run these checks:

### Health Checks

```bash
# API health endpoint
curl http://localhost:8000/health
# Expected: {"status": "healthy"}

# Admin dashboard
curl -s -o /dev/null -w "%{http_code}" http://localhost:8001/admin/
# Expected: 200

# API docs (OpenAPI)
curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/docs
# Expected: 200
```

### System Status

```bash
# Job queue status
curl http://localhost:8000/api/jobs/status
# Returns: {
#   "by_type": {"llm_approval_check": {"pending": 5, ...}, ...},
#   "by_status": {"pending": 5, "running": 0, ...},
#   "recent_errors": [...]
# }

# Config values (check workers are active)
curl http://localhost:8000/api/config/
# Returns list of all system_config entries

# Specific config key
curl http://localhost:8000/api/config/workers_active
# Returns: {"key": "workers_active", "value": true, "set_by": "seed", ...}
```

### Dashboard Verification

Open the admin dashboard at `http://localhost:8001/admin/` and verify:

- **Queue Depth** widget shows pending job counts by type
- **Error Log** widget shows recent failed jobs (may be empty after fresh deploy)
- **Source Health** widget shows approved/errored/inactive source counts
- **GPU Status** widget shows GPU service state from `system_config`
- **Rate Limits** widget shows current API usage vs limits
- **Cost Tracker** widget shows API cost rollups (may be empty initially)

### LLM Panel

Navigate to `http://localhost:8001/admin/llm/` and verify:

- Pending approvals list shows the 5 bootstrapped thinkers
- LLM status shows system health
- Override button is available for manual approval

## 3. Rollback

### Emergency Stop

Immediately halt all job processing:

```bash
curl -X PUT http://localhost:8000/api/config/workers_active \
  -H "Content-Type: application/json" \
  -d '{"value": false, "set_by": "admin"}'
```

This sets `workers_active=false` in `system_config`, which prevents all workers from claiming new jobs. Running jobs will complete but no new jobs will be started.

### Full Rollback Procedure

1. **Stop workers** (kill switch):
   ```bash
   curl -X PUT http://localhost:8000/api/config/workers_active \
     -H "Content-Type: application/json" \
     -d '{"value": false, "set_by": "admin"}'
   ```

2. **Wait for running jobs to complete.** Check via:
   ```bash
   curl http://localhost:8000/api/jobs/status
   # Wait until by_status.running == 0
   ```

3. **Revert migration** (if schema change caused issues):
   ```bash
   uv run alembic downgrade -1
   ```

4. **Redeploy previous version** of the application code

5. **Re-run migration** for the previous version:
   ```bash
   uv run alembic upgrade head
   ```

6. **Restart workers**:
   ```bash
   curl -X PUT http://localhost:8000/api/config/workers_active \
     -H "Content-Type: application/json" \
     -d '{"value": true, "set_by": "admin"}'
   ```

## 4. Common Problems

### Workers Not Processing Jobs

**Symptoms:** Jobs stay in `pending` status, no progress on queue.

**Diagnosis:**
```bash
# Check kill switch
curl http://localhost:8000/api/config/workers_active
# If value is false, workers are halted

# Check job queue status
curl http://localhost:8000/api/jobs/status
# Look at by_status counts
```

**Resolution:**
- If `workers_active` is false, re-enable:
  ```bash
  curl -X PUT http://localhost:8000/api/config/workers_active \
    -H "Content-Type: application/json" \
    -d '{"value": true, "set_by": "admin"}'
  ```
- If workers are active but not claiming, check worker process logs for connection errors
- Check if all jobs have exceeded `max_attempts` (status would be `failed`, not `pending`)

### LLM Reviews Timing Out

**Symptoms:** Thinkers/sources stuck in `pending_llm` status, jobs show `llm_timeout` error category.

**Diagnosis:**
```bash
# Check Anthropic API key
echo $ANTHROPIC_API_KEY  # Should be set

# Check LLM timeout setting
curl http://localhost:8000/api/config/llm_timeout_hours
# Default: 2 hours

# Check for LLM errors in job queue
curl http://localhost:8000/api/jobs/status
# Look for llm_approval_check jobs with error_category
```

**Resolution:**
- Verify `ANTHROPIC_API_KEY` is set and valid
- Check Anthropic API status page for outages
- If API is down, pending reviews will auto-escalate to human review after `llm_timeout_hours`
- Use admin LLM Panel at `http://localhost:8001/admin/llm/` to manually override decisions
- Adjust timeout: `PUT /api/config/llm_timeout_hours {"value": 4, "set_by": "admin"}`

### GPU Not Scaling

**Symptoms:** `process_content` jobs stuck waiting for GPU transcription.

**Diagnosis:**
```bash
# Check GPU-related config
curl http://localhost:8000/api/config/gpu_idle_timeout_minutes
curl http://localhost:8000/api/config/gpu_queue_threshold
```

**Resolution:**
- Verify `RAILWAY_API_KEY` is set and valid
- Check Railway dashboard for GPU service status
- Check `gpu_queue_threshold` config (default: 5 pending jobs triggers scale-up)
- Check `gpu_idle_timeout_minutes` config (default: 30 minutes before scale-down)
- Manually trigger GPU scale-up via Railway dashboard if needed

### Feed Polling Failures

**Symptoms:** Sources not getting new content, `fetch_podcast_feed` jobs failing.

**Diagnosis:**
```bash
# Check source health in admin dashboard
# or via API:
curl "http://localhost:8000/api/sources/?approval_status=approved"
```

**Resolution:**
- Check the source's RSS feed URL is still accessible (URLs change)
- Check the source's `error_count` -- high error counts may indicate a dead feed
- Check rate limits via admin dashboard Rate Limits widget
- Verify the source has `approval_status="approved"` (unapproved sources are not polled)

### High Error Rate

**Symptoms:** Many failed jobs, error log filling up in admin dashboard.

**Diagnosis:**
1. Open admin dashboard at `http://localhost:8001/admin/`
2. Check **Error Log** widget for recent failures
3. Look at error categories to identify patterns

**Resolution by error category:**

| Error Category | Likely Cause | Resolution |
|----------------|-------------|------------|
| `rss_parse` | Malformed RSS feed | Check feed URL, may need source update |
| `http_timeout` | External API slow | Check network, increase timeout |
| `http_error` | External API returning errors | Check API status, credentials |
| `rate_limited` | Hit API rate limit | Wait for window reset, check rate config |
| `llm_api_error` | Anthropic API issue | Check API key, status page |
| `llm_timeout` | Anthropic API slow | Increase `llm_timeout_hours` |
| `transcription_failed` | GPU service issue | Check GPU worker, Railway status |
| `payload_invalid` | Bad job data | Check job payload in database |
| `handler_not_found` | Unknown job_type | Verify handler is registered in `registry.py` |
| `database_error` | PostgreSQL issue | Check connection pool, DB logs |

### Database Connection Issues

**Symptoms:** Application startup failures, intermittent query errors.

**Diagnosis:**
```bash
# Check DATABASE_URL is set correctly
echo $DATABASE_URL

# Test direct connection
uv run python -c "
import asyncio
from sqlalchemy import text
from src.thinktank.database import engine
async def test():
    async with engine.begin() as conn:
        await conn.execute(text('SELECT 1'))
        print('Database connected')
asyncio.run(test())
"
```

**Resolution:**
- Verify `DATABASE_URL` format: `postgresql+asyncpg://user:pass@host:port/dbname`
- Check PostgreSQL is running and accepting connections
- Check connection pool settings in `src/thinktank/config.py`: `db_pool_size` (default 10), `db_max_overflow` (default 5)
- Check PostgreSQL max_connections setting (must be > pool_size * number_of_services)

## 5. Operational Commands

### System Control

```bash
# Pause all workers (kill switch)
curl -X PUT http://localhost:8000/api/config/workers_active \
  -H "Content-Type: application/json" \
  -d '{"value": false, "set_by": "admin"}'

# Resume all workers
curl -X PUT http://localhost:8000/api/config/workers_active \
  -H "Content-Type: application/json" \
  -d '{"value": true, "set_by": "admin"}'
```

### Configuration Management

```bash
# List all config
curl http://localhost:8000/api/config/

# Get specific config
curl http://localhost:8000/api/config/{key}

# Update config
curl -X PUT http://localhost:8000/api/config/{key} \
  -H "Content-Type: application/json" \
  -d '{"value": <new_value>, "set_by": "admin"}'
```

### Key Configuration Parameters

| Key | Default | Purpose |
|-----|---------|---------|
| `workers_active` | `true` (after bootstrap) | Global kill switch for job processing |
| `max_candidates_per_day` | `20` | Daily quota for candidate thinker discovery |
| `llm_timeout_hours` | `2` | Hours before pending LLM reviews escalate to human |
| `backpressure_threshold` | `100` | Queue depth that triggers discovery priority demotion |
| `gpu_idle_timeout_minutes` | `30` | Minutes before idle GPU service is scaled down |
| `gpu_queue_threshold` | `5` | Pending transcription jobs that trigger GPU scale-up |
| `discovery_priority_default` | `5` | Default priority for discovery jobs |
| `min_duration_seconds` | `600` | Minimum episode duration (10 min) to avoid shorts |
| `reclaim_interval_seconds` | `300` | How often to check for stale running jobs |
| `stale_job_threshold_minutes` | `30` | How long before a running job is considered stale |

### Cost Monitoring

- **Admin dashboard Cost Tracker** widget at `http://localhost:8001/admin/` shows 24-hour API cost rollups
- **Rate Limits widget** shows current usage vs configured limits per external API
- The `rollup_api_usage` handler aggregates `rate_limit_usage` into `api_usage` hourly

### Manual LLM Override

When LLM reviews are pending and you need to make manual decisions:

1. Open `http://localhost:8001/admin/llm/`
2. Find the pending approval in the list
3. Click **Override** button
4. Select decision (approve/reject) and provide reasoning
5. The override is logged in `llm_reviews` with `set_by="human_override"`

### Category Management

Manage the thinker category taxonomy:

1. Open `http://localhost:8001/admin/categories/`
2. View the hierarchical category tree
3. Create new categories with parent selection
4. Edit existing category names and descriptions
5. Delete categories (blocked if they have children or assigned thinkers)

Or re-seed the full taxonomy:
```bash
uv run python -m scripts.seed_categories
```

## 6. Service Architecture

ThinkTank runs as 4 Railway services:

| Service | Port | Dockerfile | Purpose |
|---------|------|-----------|---------|
| API | 8000 | `Dockerfile.api` | REST API endpoints (`src/thinktank/api/main.py`) |
| Admin | 8001 | `Dockerfile.admin` | HTMX dashboard (`src/thinktank/admin/main.py`) |
| CPU Worker | -- | `Dockerfile.worker-cpu` | Job queue processing (`src/thinktank/worker/`) |
| GPU Worker | -- | `Dockerfile.worker-gpu` | Parakeet transcription (`src/thinktank/gpu_worker/`) |

### Environment Variables

| Variable | Required | Purpose |
|----------|----------|---------|
| `DATABASE_URL` | Yes | PostgreSQL connection string |
| `ANTHROPIC_API_KEY` | Yes (for LLM) | Claude API key for governance |
| `RAILWAY_API_KEY` | Yes (for GPU) | Railway API for GPU scaling |
| `LOG_LEVEL` | No | Logging level (default: INFO) |
| `DEBUG` | No | Debug mode (default: false) |
| `SERVICE_NAME` | No | Service identifier for logs |
