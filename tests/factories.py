"""Factory functions for all 14 ThinkTank model types.

Each model has two factory variants:
- make_{model}(**overrides) -> Model: Creates an in-memory instance with sensible defaults.
- create_{model}(session, **overrides) -> Model: Persists to DB via session.flush().

Every field is overridable. UUIDs and slugs/URLs are auto-generated to be unique.
"""

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from src.thinktank.models import (
    ApiUsage,
    CandidateThinker,
    Category,
    Content,
    ContentThinker,
    Job,
    LLMReview,
    RateLimitUsage,
    Source,
    SystemConfig,
    Thinker,
    ThinkerCategory,
    ThinkerMetrics,
    ThinkerProfile,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _hex8() -> str:
    return uuid.uuid4().hex[:8]


# ---------- Category (spec 3.1) ----------


def make_category(**overrides: Any) -> Category:
    """Create a Category with sensible defaults. Override any field."""
    defaults: dict[str, Any] = {
        "id": uuid.uuid4(),
        "slug": f"test-cat-{_hex8()}",
        "name": "Test Category",
        "description": "A test category for testing.",
        "created_at": _now(),
    }
    defaults.update(overrides)
    return Category(**defaults)


async def create_category(session: AsyncSession, **overrides: Any) -> Category:
    """Create and persist a Category. Returns the persisted instance."""
    cat = make_category(**overrides)
    session.add(cat)
    await session.flush()
    return cat


# ---------- Thinker (spec 3.2) ----------


def make_thinker(**overrides: Any) -> Thinker:
    """Create a Thinker with sensible defaults. Override any field."""
    defaults: dict[str, Any] = {
        "id": uuid.uuid4(),
        "name": "Test Thinker",
        "slug": f"test-thinker-{_hex8()}",
        "tier": 2,
        "bio": "A test thinker for testing.",
        "approval_status": "approved",
        "active": True,
        "added_at": _now(),
    }
    defaults.update(overrides)
    return Thinker(**defaults)


async def create_thinker(session: AsyncSession, **overrides: Any) -> Thinker:
    """Create and persist a Thinker. Returns the persisted instance."""
    thinker = make_thinker(**overrides)
    session.add(thinker)
    await session.flush()
    return thinker


# ---------- ThinkerCategory (spec 3.3) ----------


def make_thinker_category(**overrides: Any) -> ThinkerCategory:
    """Create a ThinkerCategory junction entry. Requires thinker_id and category_id."""
    defaults: dict[str, Any] = {
        "relevance": 5,
        "added_at": _now(),
    }
    defaults.update(overrides)
    return ThinkerCategory(**defaults)


async def create_thinker_category(session: AsyncSession, **overrides: Any) -> ThinkerCategory:
    """Create and persist a ThinkerCategory. Returns the persisted instance."""
    tc = make_thinker_category(**overrides)
    session.add(tc)
    await session.flush()
    return tc


# ---------- ThinkerProfile (spec 3.4) ----------


def make_thinker_profile(**overrides: Any) -> ThinkerProfile:
    """Create a ThinkerProfile with empty JSONB defaults. Requires thinker_id."""
    defaults: dict[str, Any] = {
        "id": uuid.uuid4(),
        "education": [],
        "positions_held": [],
        "notable_works": [],
        "awards": [],
        "updated_at": _now(),
    }
    defaults.update(overrides)
    return ThinkerProfile(**defaults)


async def create_thinker_profile(session: AsyncSession, **overrides: Any) -> ThinkerProfile:
    """Create and persist a ThinkerProfile. Returns the persisted instance."""
    tp = make_thinker_profile(**overrides)
    session.add(tp)
    await session.flush()
    return tp


# ---------- ThinkerMetrics (spec 3.5) ----------


def make_thinker_metrics(**overrides: Any) -> ThinkerMetrics:
    """Create a ThinkerMetrics snapshot. Requires thinker_id."""
    defaults: dict[str, Any] = {
        "id": uuid.uuid4(),
        "platform": "podcast",
        "handle": "test_handle",
        "followers": 0,
        "avg_views": 0,
        "post_count": 0,
        "verified": False,
        "snapshotted_at": _now(),
    }
    defaults.update(overrides)
    return ThinkerMetrics(**defaults)


async def create_thinker_metrics(session: AsyncSession, **overrides: Any) -> ThinkerMetrics:
    """Create and persist a ThinkerMetrics. Returns the persisted instance."""
    tm = make_thinker_metrics(**overrides)
    session.add(tm)
    await session.flush()
    return tm


# ---------- Source (spec 3.6) ----------


def make_source(**overrides: Any) -> Source:
    """Create a Source with sensible defaults. Requires thinker_id."""
    defaults: dict[str, Any] = {
        "id": uuid.uuid4(),
        "source_type": "podcast_rss",
        "name": "Test Podcast",
        "url": f"https://example.com/feed/{_hex8()}.xml",
        "config": {},
        "approval_status": "approved",
        "backfill_complete": False,
        "item_count": 0,
        "active": True,
        "error_count": 0,
        "created_at": _now(),
    }
    defaults.update(overrides)
    return Source(**defaults)


async def create_source(session: AsyncSession, **overrides: Any) -> Source:
    """Create and persist a Source. Returns the persisted instance."""
    source = make_source(**overrides)
    session.add(source)
    await session.flush()
    return source


# ---------- Content (spec 3.7) ----------


def make_content(**overrides: Any) -> Content:
    """Create a Content item. Requires source_id and source_owner_id."""
    hex_id = _hex8()
    defaults: dict[str, Any] = {
        "id": uuid.uuid4(),
        "content_type": "episode",
        "url": f"https://example.com/episodes/{hex_id}",
        "canonical_url": f"https://example.com/episodes/{hex_id}",
        "title": f"Test Episode {hex_id}",
        "status": "pending",
        "discovered_at": _now(),
    }
    defaults.update(overrides)
    return Content(**defaults)


async def create_content(session: AsyncSession, **overrides: Any) -> Content:
    """Create and persist a Content item. Returns the persisted instance."""
    content = make_content(**overrides)
    session.add(content)
    await session.flush()
    return content


# ---------- ContentThinker (spec 3.8) ----------


def make_content_thinker(**overrides: Any) -> ContentThinker:
    """Create a ContentThinker attribution. Requires content_id and thinker_id."""
    defaults: dict[str, Any] = {
        "role": "primary",
        "confidence": 10,
        "added_at": _now(),
    }
    defaults.update(overrides)
    return ContentThinker(**defaults)


async def create_content_thinker(session: AsyncSession, **overrides: Any) -> ContentThinker:
    """Create and persist a ContentThinker. Returns the persisted instance."""
    ct = make_content_thinker(**overrides)
    session.add(ct)
    await session.flush()
    return ct


# ---------- CandidateThinker (spec 3.9) ----------


def make_candidate_thinker(**overrides: Any) -> CandidateThinker:
    """Create a CandidateThinker with sensible defaults."""
    defaults: dict[str, Any] = {
        "id": uuid.uuid4(),
        "name": "Test Candidate",
        "normalized_name": "test candidate",
        "appearance_count": 1,
        "first_seen_at": _now(),
        "last_seen_at": _now(),
        "status": "pending_llm",
    }
    defaults.update(overrides)
    return CandidateThinker(**defaults)


async def create_candidate_thinker(session: AsyncSession, **overrides: Any) -> CandidateThinker:
    """Create and persist a CandidateThinker. Returns the persisted instance."""
    ct = make_candidate_thinker(**overrides)
    session.add(ct)
    await session.flush()
    return ct


# ---------- Job (spec 3.10) ----------


def make_job(**overrides: Any) -> Job:
    """Create a Job with sensible defaults."""
    defaults: dict[str, Any] = {
        "id": uuid.uuid4(),
        "job_type": "discover_thinker",
        "payload": {},
        "status": "pending",
        "priority": 5,
        "attempts": 0,
        "max_attempts": 3,
        "created_at": _now(),
    }
    defaults.update(overrides)
    return Job(**defaults)


async def create_job(session: AsyncSession, **overrides: Any) -> Job:
    """Create and persist a Job. Returns the persisted instance."""
    job = make_job(**overrides)
    session.add(job)
    await session.flush()
    return job


# ---------- LLMReview (spec 3.11) ----------


def make_llm_review(**overrides: Any) -> LLMReview:
    """Create an LLMReview with sensible defaults."""
    defaults: dict[str, Any] = {
        "id": uuid.uuid4(),
        "review_type": "thinker_approval",
        "trigger": "job_gate",
        "context_snapshot": {},
        "prompt_used": "Test prompt for review.",
        "created_at": _now(),
    }
    defaults.update(overrides)
    return LLMReview(**defaults)


async def create_llm_review(session: AsyncSession, **overrides: Any) -> LLMReview:
    """Create and persist an LLMReview. Returns the persisted instance."""
    review = make_llm_review(**overrides)
    session.add(review)
    await session.flush()
    return review


# ---------- SystemConfig (spec 3.12) ----------


def make_system_config(**overrides: Any) -> SystemConfig:
    """Create a SystemConfig entry. Uses unique key by default."""
    defaults: dict[str, Any] = {
        "key": f"test_key_{_hex8()}",
        "value": {"enabled": True},
        "set_by": "seed",
        "updated_at": _now(),
    }
    defaults.update(overrides)
    return SystemConfig(**defaults)


async def create_system_config(session: AsyncSession, **overrides: Any) -> SystemConfig:
    """Create and persist a SystemConfig. Returns the persisted instance."""
    sc = make_system_config(**overrides)
    session.add(sc)
    await session.flush()
    return sc


# ---------- RateLimitUsage (spec 3.13) ----------


def make_rate_limit_usage(**overrides: Any) -> RateLimitUsage:
    """Create a RateLimitUsage entry."""
    defaults: dict[str, Any] = {
        "id": uuid.uuid4(),
        "api_name": "test_api",
        "worker_id": "worker-1",
        "called_at": _now(),
    }
    defaults.update(overrides)
    return RateLimitUsage(**defaults)


async def create_rate_limit_usage(session: AsyncSession, **overrides: Any) -> RateLimitUsage:
    """Create and persist a RateLimitUsage. Returns the persisted instance."""
    rl = make_rate_limit_usage(**overrides)
    session.add(rl)
    await session.flush()
    return rl


# ---------- ApiUsage (spec 3.14) ----------


def make_api_usage(**overrides: Any) -> ApiUsage:
    """Create an ApiUsage entry."""
    defaults: dict[str, Any] = {
        "id": uuid.uuid4(),
        "api_name": "test_api",
        "endpoint": "search",
        "period_start": _now(),
        "call_count": 1,
    }
    defaults.update(overrides)
    return ApiUsage(**defaults)


async def create_api_usage(session: AsyncSession, **overrides: Any) -> ApiUsage:
    """Create and persist an ApiUsage. Returns the persisted instance."""
    au = make_api_usage(**overrides)
    session.add(au)
    await session.flush()
    return au
