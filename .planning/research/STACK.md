# Technology Stack

**Project:** ThinkTank - Global Intelligence Ingestion Platform
**Researched:** 2026-03-08
**Overall Confidence:** HIGH

---

## Recommended Stack

### Core Framework

| Technology | Version | Purpose | Why | Confidence |
|------------|---------|---------|-----|------------|
| Python | 3.12 | Runtime | Specified in constraints. Stable, mature asyncio, full NeMo/CUDA support. Avoid 3.13+ until NeMo ecosystem catches up. | HIGH |
| FastAPI | >=0.135.1 | API framework | Async-native, automatic OpenAPI docs, Pydantic integration, SSE support for dashboard. Dominant Python API framework. | HIGH |
| Uvicorn | >=0.41.0 | ASGI server | Standard FastAPI deployment server. Install with `[standard]` extras for uvloop + httptools. | HIGH |
| Pydantic | >=2.12.5 | Data validation | FastAPI's native validation layer. V2 is 5-50x faster than V1. Used for request/response models, job payloads, LLM response parsing. | HIGH |
| pydantic-settings | >=2.13.1 | Configuration | Environment variable loading with type validation, `.env` support, nested config. Standard for FastAPI projects. | HIGH |

### Database & ORM

| Technology | Version | Purpose | Why | Confidence |
|------------|---------|---------|-----|------------|
| PostgreSQL | 16 | Primary database | Railway-managed. All state: content, jobs, reviews, metrics. `SELECT FOR UPDATE SKIP LOCKED` for job queue. `pg_trgm` for candidate dedup. | HIGH |
| SQLAlchemy | >=2.0.48 | ORM / query builder | Async support via `AsyncSession`, declarative mapping with type hints, mature Alembic integration. Use 2.0-style queries exclusively. | HIGH |
| asyncpg | (via SQLAlchemy) | PostgreSQL driver | Fastest async PostgreSQL driver. 3-4x lower latency than psycopg2 in async workloads. SQLAlchemy dialect: `postgresql+asyncpg`. | HIGH |
| Alembic | >=1.17.2 | Schema migrations | Only migration tool for SQLAlchemy. Forward-only, auto-generate from models, run on startup. Use async engine config. | HIGH |

### HTTP & Networking

| Technology | Version | Purpose | Why | Confidence |
|------------|---------|---------|-----|------------|
| httpx | >=0.28.1 | HTTP client | Async-native, connection pooling, timeout control, HTTP/2 support. Used for RSS fetching, API calls to Listen Notes/Podcast Index/YouTube. | HIGH |
| feedparser | >=6.0.12 | RSS/Atom parsing | De facto standard for RSS parsing in Python. Handles RSS 0.9x through 2.0, Atom, CDF. Battle-tested, no serious alternative exists. | HIGH |

### Transcription & Audio

| Technology | Version | Purpose | Why | Confidence |
|------------|---------|---------|-----|------------|
| NVIDIA NeMo Toolkit | >=2.6.x (install via `nemo_toolkit[asr]`) | ASR framework | Required for Parakeet TDT 1.1B inference. Handles model loading, audio preprocessing, batch inference. | HIGH |
| Parakeet TDT 1.1B | - | Primary transcription model | RTFx >2,000 (fastest open ASR model). ~4GB VRAM on L4. English-only, which matches the use case. Speed is the priority for batch transcription at scale. See Alternatives section for accuracy vs speed tradeoff. | HIGH |
| yt-dlp | >=2025.12.08 | Audio extraction / YouTube captions | Pin to 2025.12.08 or a known-good version. The 2026.03.03 release broke DASH audio-only format extraction. Use for own-channel YouTube audio and `--write-auto-sub` for captions. | MEDIUM |
| soundfile | >=0.13.1 | Audio I/O | Reads/writes WAV, FLAC, OGG via libsndfile. Used for 16kHz mono WAV conversion before Parakeet inference. | HIGH |
| ffmpeg | system package | Audio conversion | Installed in container. Convert podcast audio formats to 16kHz mono WAV. Required by yt-dlp for format merging. | HIGH |

**Note on yt-dlp version pinning:** The yt-dlp 2026.03.03 release has a known regression where DASH audio-only formats are missing (GitHub issue #16128). Pin to 2025.12.08 until this is resolved, or test each update before upgrading.

### LLM Integration

| Technology | Version | Purpose | Why | Confidence |
|------------|---------|---------|-----|------------|
| anthropic | >=0.84.0 | Anthropic Claude SDK | Official Python SDK. Async support, structured output parsing, token counting. Used for LLM Supervisor calls. | HIGH |

### External APIs (Client Libraries)

| Technology | Version | Purpose | Why | Confidence |
|------------|---------|---------|-----|------------|
| python-podcastindex | >=1.15.0 | Podcast Index API | Thin wrapper for podcast feed discovery. Free API. Provides search by person and by feed. | MEDIUM |
| podcast-api (Listen Notes) | latest | Listen Notes API | Official SDK for guest discovery. Free tier: 10K req/mo. Primary podcast guest search. | MEDIUM |

**Note on API clients:** Both podcast API libraries are thin HTTP wrappers. If either becomes unmaintained or problematic, replace with direct `httpx` calls against the REST APIs -- the API surfaces are simple enough that a dedicated SDK is convenient but not essential.

### Admin Dashboard

| Technology | Version | Purpose | Why | Confidence |
|------------|---------|---------|-----|------------|
| HTMX | >=2.0.8 | Frontend interactivity | Server-rendered HTML with dynamic updates. No build step, no JavaScript framework. 10-second polling for queue depth. Perfect for admin dashboards where DX simplicity beats SPA complexity. | HIGH |
| Jinja2 | >=3.1.6 | HTML templating | FastAPI's native template engine. Async-compatible. Used for all admin dashboard pages. | HIGH |
| TailwindCSS | 3.x (via CDN) | Styling | Utility-first CSS. Use CDN for admin dashboard -- no build pipeline needed for internal tool. Pair with a component set like DaisyUI if desired. | MEDIUM |

**Why HTMX over React/Vue/Svelte:** The admin dashboard is an internal monitoring tool, not a consumer product. HTMX eliminates the entire frontend build pipeline (Node.js, bundler, framework deps) while providing the reactive behavior needed for live queue depth, job status, and rate limit gauges. Server-side rendering means zero API duplication -- FastAPI routes return HTML fragments directly.

### Observability

| Technology | Version | Purpose | Why | Confidence |
|------------|---------|---------|-----|------------|
| structlog | >=25.5.0 | Structured logging | JSON logging out of the box. Context variables for correlation IDs. Async-compatible. Production-proven since 2013. Directly satisfies STANDARDS.md logging requirements. | HIGH |

### Code Quality & Tooling

| Technology | Version | Purpose | Why | Confidence |
|------------|---------|---------|-----|------------|
| ruff | >=0.15.3 | Linter + formatter | Replaces Black, Flake8, isort, pyupgrade in one tool. 10-100x faster. Written in Rust. One config in `pyproject.toml`. Satisfies "one formatter, one linter, zero debates" standard. | HIGH |
| mypy | >=1.19.1 | Type checker | Static type checking for all public interfaces. Run in CI. 40% faster than previous versions with new binary cache. | HIGH |
| uv | latest | Package manager | 10-100x faster than pip. Deterministic lockfile (`uv.lock`). Replaces pip, pip-tools, virtualenv. Use `uv pip compile` for requirements.txt if needed for Docker. | HIGH |

### Testing

| Technology | Version | Purpose | Why | Confidence |
|------------|---------|---------|-----|------------|
| pytest | >=8.x | Test framework | Standard Python test framework. Plugin ecosystem, fixtures, parametrize. | HIGH |
| pytest-asyncio | >=1.3.0 | Async test support | Required for testing async FastAPI handlers, job processors, DB operations. Use `loop_scope` configuration (1.0+ API). | HIGH |
| pytest-httpx | latest | HTTP mocking | Mocks httpx requests in tests. Fixture-based, supports async. Essential for mocking Listen Notes, Podcast Index, YouTube API calls. | HIGH |
| factory-boy | >=3.3.3 | Test data factories | Generates valid domain objects (thinkers, sources, content, jobs) with sensible defaults. SQLAlchemy integration built-in. Directly satisfies "test data is generated, not static" standard. | HIGH |
| testcontainers | latest | Test PostgreSQL | Spins up real PostgreSQL in Docker for integration tests. Satisfies "database tests use the real engine" standard. | HIGH |

### Infrastructure & Deployment

| Technology | Version | Purpose | Why | Confidence |
|------------|---------|---------|-----|------------|
| Railway | - | Cloud platform | Managed PostgreSQL, GPU instances (L4), auto-deploy from git, private networking between services. Specified in constraints. | HIGH |
| Docker | - | Containerization | Standard container for API, worker-cpu, admin services. GPU worker uses `nvcr.io/nvidia/nemo:24.05` base image. | HIGH |
| `nvcr.io/nvidia/nemo:24.05` | 24.05 | GPU container base | Pre-installed CUDA, PyTorch, NeMo. Avoids complex GPU driver setup. Specified in constraints. | HIGH |

---

## Alternatives Considered

### Transcription Model

| Category | Recommended | Alternative | Why Not Alternative |
|----------|-------------|-------------|---------------------|
| Transcription | Parakeet TDT 1.1B | Canary Qwen 2.5B | Canary has better WER (5.63% vs ~8.0%) but RTFx of 418 vs >2,000 for Parakeet. For batch podcast transcription at scale, throughput matters more than marginal accuracy gains. Parakeet uses ~4GB VRAM vs ~8GB. **However:** if accuracy becomes a concern post-launch, Canary Qwen fits on the same L4 (24GB VRAM) and can be swapped in. Consider a two-model strategy: Parakeet for bulk backfill, Canary for Tier 1 thinkers. |
| Transcription | Parakeet TDT 1.1B | Whisper Large V3 | Whisper is 99+ languages but English-only needed. Much slower RTFx (~50-100 on L4). Higher VRAM (~10GB). Only advantage is multilingual, which is out of scope. |
| Transcription API | Self-hosted Parakeet | Deepgram/AssemblyAI API | API transcription costs $0.01-0.05/minute. At scale (thousands of hours), self-hosted GPU is dramatically cheaper. Monthly GPU cost ~$100-200 vs potentially $1,000+ in API fees. |

### Job Queue

| Category | Recommended | Alternative | Why Not Alternative |
|----------|-------------|-------------|---------------------|
| Job queue | Custom `SELECT FOR UPDATE SKIP LOCKED` | pgqueuer library | pgqueuer adds a dependency for something that's ~50 lines of SQL. The spec defines a custom jobs table schema with domain-specific fields (llm_review_id, error_category, etc.) that don't map to pgqueuer's schema. Custom implementation gives full control. |
| Job queue | PostgreSQL-backed | Celery + Redis | Adds Redis infrastructure dependency. The spec explicitly prohibits Redis. PostgreSQL SKIP LOCKED handles the required throughput (tens of jobs/second, not thousands). |
| Job queue | PostgreSQL-backed | Dramatiq + Redis | Same Redis prohibition. Also less mature than Celery with worse documentation. |
| Job queue | PostgreSQL-backed | ARQ (async Redis queue) | Still requires Redis. |

### HTTP Client

| Category | Recommended | Alternative | Why Not Alternative |
|----------|-------------|-------------|---------------------|
| HTTP | httpx | aiohttp | httpx has cleaner API, better timeout handling, HTTP/2 support, and the `httpx.AsyncClient` context manager pattern is more ergonomic. aiohttp is legacy at this point for new projects. |
| HTTP | httpx | requests | Not async-native. Would require `run_in_executor` wrapping. httpx provides the same API surface with async support. |

### Admin Dashboard

| Category | Recommended | Alternative | Why Not Alternative |
|----------|-------------|-------------|---------------------|
| Admin frontend | HTMX + Jinja2 | React/Next.js | Massive overkill for an internal admin tool. Adds Node.js toolchain, build step, API duplication. HTMX delivers 90% of SPA feel with 10% of the complexity. |
| Admin frontend | HTMX + Jinja2 | Streamlit/Gradio | Not production-grade for always-on dashboards. Poor control over layout, no WebSocket/SSE integration, slow initial load. |
| Admin frontend | HTMX + Jinja2 | Django Admin | Would require Django, replacing the entire FastAPI stack. Overkill dependency for one admin page. |

### Package Management

| Category | Recommended | Alternative | Why Not Alternative |
|----------|-------------|-------------|---------------------|
| Packages | uv | pip + pip-tools | uv is 10-100x faster, handles venvs, has native lockfile support, and is from the same team as ruff (Astral). pip-tools works but is measurably slower and lacks integrated venv management. |
| Packages | uv | poetry | Poetry is slower, has had repeated dependency resolution issues, and the lock format is opaque. uv is the clear successor for new projects in 2025/2026. |

### Linting & Formatting

| Category | Recommended | Alternative | Why Not Alternative |
|----------|-------------|-------------|---------------------|
| Lint + Format | ruff | Black + Flake8 + isort | ruff replaces all three in a single tool that runs 100x faster. No reason to use separate tools when ruff covers all rules. |

### Logging

| Category | Recommended | Alternative | Why Not Alternative |
|----------|-------------|-------------|---------------------|
| Logging | structlog | stdlib logging + JSON formatter | structlog's context binding (`log = log.bind(thinker_id=...)`) is dramatically cleaner than stdlib's `LoggerAdapter`. Built-in JSON rendering, async support, and processor pipeline. |
| Logging | structlog | loguru | loguru lacks structured context binding. Pretty output but not designed for machine-parseable JSON in production. |

---

## Installation

```bash
# Use uv for package management
pip install uv

# Create virtual environment
uv venv --python 3.12

# Core application
uv pip install \
  "fastapi>=0.135.1" \
  "uvicorn[standard]>=0.41.0" \
  "pydantic>=2.12.5" \
  "pydantic-settings>=2.13.1" \
  "sqlalchemy[asyncio]>=2.0.48" \
  "asyncpg" \
  "alembic>=1.17.2" \
  "httpx>=0.28.1" \
  "feedparser>=6.0.12" \
  "anthropic>=0.84.0" \
  "structlog>=25.5.0" \
  "jinja2>=3.1.6" \
  "python-podcastindex>=1.15.0" \
  "podcast-api" \
  "soundfile>=0.13.1"

# yt-dlp (pin to known-good version)
uv pip install "yt-dlp==2025.12.8"

# Dev dependencies
uv pip install \
  "pytest>=8.0" \
  "pytest-asyncio>=1.3.0" \
  "pytest-httpx" \
  "factory-boy>=3.3.3" \
  "testcontainers[postgres]" \
  "ruff>=0.15.3" \
  "mypy>=1.19.1"

# GPU worker (separate requirements, installed in nvcr.io/nvidia/nemo:24.05 container)
# nemo_toolkit[asr] is pre-installed in the NeMo container
# Only install the application code and its non-ML dependencies
```

### Docker Base Images

```dockerfile
# API, Worker-CPU, Admin services
FROM python:3.12-slim

# GPU Worker
FROM nvcr.io/nvidia/nemo:24.05
# NeMo, PyTorch, CUDA pre-installed
# Install application dependencies on top
```

### System Dependencies

```bash
# Required on all service containers
apt-get install -y ffmpeg libsndfile1

# ffmpeg: audio format conversion (podcast formats -> 16kHz mono WAV)
# libsndfile1: required by soundfile Python package
```

---

## Version Pinning Strategy

| Category | Strategy | Rationale |
|----------|----------|-----------|
| Core framework (FastAPI, SQLAlchemy, Pydantic) | Pin minimum, allow patch updates | These are stable, well-tested libraries. Patch updates are safe. |
| NeMo / ML stack | Pin exact in GPU container | ML frameworks have breaking changes between minor versions. Lock to container version. |
| yt-dlp | Pin exact version | Frequent releases with occasional regressions (e.g., 2026.03.03 DASH bug). Test before upgrading. |
| External API SDKs | Pin minimum | Thin wrappers, unlikely to break. |
| Dev tools (ruff, mypy, pytest) | Pin minimum | Dev tools don't affect production. Let them update. |

Use `uv lock` to generate a deterministic lockfile. Commit `uv.lock` to the repository. This satisfies the "dependencies are locked" standard from STANDARDS.md.

---

## Key Architecture Notes for Stack Decisions

### Why No Redis

The spec explicitly prohibits Redis. PostgreSQL's `SELECT FOR UPDATE SKIP LOCKED` provides:
- Atomic job claiming without contention
- Priority-based ordering via `ORDER BY priority, created_at`
- Transactional consistency with application data (a job and its associated content row are created in the same transaction)
- Rate limit coordination via `rate_limit_usage` table
- Throughput ceiling of ~10,000 jobs/second, far exceeding this system's needs (tens of jobs/second)

The only scenario where Redis would be needed is sub-millisecond pub/sub for real-time notifications, which this system does not require. HTMX polling at 10-second intervals is sufficient for dashboard updates.

### Why asyncpg Over psycopg (async)

Both are viable. asyncpg is chosen because:
1. It is the recommended async PostgreSQL driver in SQLAlchemy's documentation
2. Benchmark data shows 3-4x lower latency in async workloads
3. It has been the default async PostgreSQL driver for FastAPI projects since 2020
4. psycopg 3's async mode is newer and has less production history

### Why Custom Job Queue Over pgqueuer

pgqueuer is a well-designed library, but the ThinkTank job schema has domain-specific requirements that don't map cleanly:
- `llm_review_id` foreign key for LLM approval gating
- `error_category` for structured error classification
- `awaiting_llm` and `rejected_by_llm` statuses
- Backpressure mechanism based on cross-job-type queue depth
- Rate limit coordination integrated into job claiming

Building the queue directly gives full control over these semantics. The core pattern is ~50 lines of SQL.

### Parakeet vs Canary: Speed vs Accuracy Tradeoff

The spec calls for Parakeet TDT 1.1B, which is the correct choice for the initial launch:

| Factor | Parakeet TDT 1.1B | Canary Qwen 2.5B |
|--------|-------------------|-------------------|
| WER | ~8.0% | 5.63% |
| RTFx | >2,000 | 418 |
| VRAM | ~4GB | ~8GB |
| Fits L4 (24GB) | Yes (6x headroom) | Yes (3x headroom) |
| Batch throughput | ~40x real-time | ~7x real-time |

For initial backfill of thousands of podcast episodes, Parakeet's 5x speed advantage means dramatically lower GPU costs. A 1-hour podcast takes ~1.5 seconds vs ~8.5 seconds. Over 10,000 episodes averaging 60 minutes, that is 4.2 GPU-hours vs 23.6 GPU-hours.

**Phase 2 consideration:** Once backfill is complete and the system enters incremental mode (a few dozen episodes per day), switching to Canary Qwen 2.5B for better accuracy becomes cost-neutral. The architecture should abstract the model choice behind a configuration flag.

---

## Sources

- [FastAPI PyPI](https://pypi.org/project/fastapi/) - Version 0.135.1
- [FastAPI Release Notes](https://fastapi.tiangolo.com/release-notes/)
- [SQLAlchemy 2.0 Changelog](https://docs.sqlalchemy.org/en/20/changelog/changelog_20.html) - Version 2.0.48
- [SQLAlchemy PostgreSQL Async](https://docs.sqlalchemy.org/en/20/dialects/postgresql.html)
- [Alembic Documentation](https://alembic.sqlalchemy.org/) - Version 1.17.2
- [Pydantic Changelog](https://docs.pydantic.dev/latest/changelog/) - Version 2.12.5
- [pydantic-settings PyPI](https://pypi.org/project/pydantic-settings/) - Version 2.13.1
- [httpx Documentation](https://www.python-httpx.org/) - Version 0.28.1
- [feedparser Documentation](https://feedparser.readthedocs.io/en/stable/) - Version 6.0.12
- [Uvicorn Release Notes](https://uvicorn.dev/release-notes/) - Version 0.41.0
- [NVIDIA Parakeet TDT 1.1B on HuggingFace](https://huggingface.co/nvidia/parakeet-tdt-1.1b)
- [NVIDIA Canary Qwen 2.5B on HuggingFace](https://huggingface.co/nvidia/canary-qwen-2.5b)
- [Best Open Source STT Models 2026 - Northflank Benchmarks](https://northflank.com/blog/best-open-source-speech-to-text-stt-model-in-2026-benchmarks)
- [NVIDIA NeMo GitHub](https://github.com/NVIDIA-NeMo/NeMo)
- [Anthropic Python SDK](https://github.com/anthropics/anthropic-sdk-python) - Version 0.84.0
- [yt-dlp GitHub](https://github.com/yt-dlp/yt-dlp) - Pin to 2025.12.08
- [yt-dlp DASH regression issue #16128](https://github.com/yt-dlp/yt-dlp/issues/16128)
- [structlog Documentation](https://www.structlog.org/en/stable/) - Version 25.5.0
- [ruff GitHub](https://github.com/astral-sh/ruff) - Version 0.15.3
- [mypy Documentation](https://mypy.readthedocs.io/) - Version 1.19.1
- [HTMX Releases](https://github.com/bigskysoftware/htmx/releases) - Version 2.0.8
- [Jinja2 PyPI](https://pypi.org/project/Jinja2/) - Version 3.1.6
- [soundfile Documentation](https://python-soundfile.readthedocs.io/) - Version 0.13.1
- [python-podcastindex PyPI](https://pypi.org/project/python-podcastindex/) - Version 1.15.0
- [Listen Notes API Python SDK](https://github.com/ListenNotes/podcast-api-python)
- [factory-boy PyPI](https://pypi.org/project/factory-boy/) - Version 3.3.3
- [pytest-asyncio PyPI](https://pypi.org/project/pytest-asyncio/) - Version 1.3.0
- [pgqueuer GitHub](https://github.com/janbjorge/pgqueuer) - Considered and rejected
- [PostgreSQL SKIP LOCKED Best Practices](https://www.inferable.ai/blog/posts/postgres-skip-locked)
- [NVIDIA L4 GPU Specifications](https://gpucompare.com/gpus/nvidia-l4-24gb) - 24GB VRAM
