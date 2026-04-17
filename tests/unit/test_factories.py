"""Unit tests for factory functions.

Tests verify:
1. Each make_* returns a valid model instance with expected defaults
2. Overrides work for every factory
3. Repeated calls produce unique IDs
4. FK-dependent factories work with valid references
"""

import uuid

from tests.factories import (
    make_api_usage,
    make_candidate_thinker,
    make_category,
    make_content,
    make_content_thinker,
    make_job,
    make_llm_review,
    make_rate_limit_usage,
    make_source,
    make_system_config,
    make_thinker,
    make_thinker_category,
    make_thinker_metrics,
    make_thinker_profile,
)
from thinktank.models import (
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

# ---------- Category ----------


class TestMakeCategory:
    def test_returns_category_instance(self):
        cat = make_category()
        assert isinstance(cat, Category)

    def test_has_sensible_defaults(self):
        cat = make_category()
        assert cat.slug is not None
        assert cat.name == "Test Category"
        assert cat.description is not None
        assert cat.id is not None

    def test_override_name(self):
        cat = make_category(name="Custom Name")
        assert cat.name == "Custom Name"

    def test_unique_ids(self):
        c1 = make_category()
        c2 = make_category()
        assert c1.id != c2.id

    def test_unique_slugs(self):
        c1 = make_category()
        c2 = make_category()
        assert c1.slug != c2.slug


# ---------- Thinker ----------


class TestMakeThinker:
    def test_returns_thinker_instance(self):
        t = make_thinker()
        assert isinstance(t, Thinker)

    def test_has_sensible_defaults(self):
        t = make_thinker()
        assert t.name == "Test Thinker"
        assert t.tier == 2
        assert t.approval_status == "approved"
        assert t.active is True
        assert t.slug is not None
        assert t.bio is not None

    def test_override_name(self):
        t = make_thinker(name="Custom Thinker")
        assert t.name == "Custom Thinker"

    def test_override_tier(self):
        t = make_thinker(tier=1)
        assert t.tier == 1

    def test_unique_ids(self):
        t1 = make_thinker()
        t2 = make_thinker()
        assert t1.id != t2.id

    def test_unique_slugs(self):
        t1 = make_thinker()
        t2 = make_thinker()
        assert t1.slug != t2.slug


# ---------- ThinkerCategory ----------


class TestMakeThinkerCategory:
    def test_returns_instance(self):
        tc = make_thinker_category(thinker_id=uuid.uuid4(), category_id=uuid.uuid4())
        assert isinstance(tc, ThinkerCategory)

    def test_default_relevance(self):
        tc = make_thinker_category(thinker_id=uuid.uuid4(), category_id=uuid.uuid4())
        assert tc.relevance == 5

    def test_override_relevance(self):
        tc = make_thinker_category(thinker_id=uuid.uuid4(), category_id=uuid.uuid4(), relevance=9)
        assert tc.relevance == 9


# ---------- ThinkerProfile ----------


class TestMakeThinkerProfile:
    def test_returns_instance(self):
        tp = make_thinker_profile(thinker_id=uuid.uuid4())
        assert isinstance(tp, ThinkerProfile)

    def test_default_jsonb_fields(self):
        tp = make_thinker_profile(thinker_id=uuid.uuid4())
        assert tp.education == []
        assert tp.positions_held == []
        assert tp.notable_works == []
        assert tp.awards == []

    def test_override_education(self):
        edu = [{"school": "MIT", "degree": "PhD"}]
        tp = make_thinker_profile(thinker_id=uuid.uuid4(), education=edu)
        assert tp.education == edu


# ---------- ThinkerMetrics ----------


class TestMakeThinkerMetrics:
    def test_returns_instance(self):
        tm = make_thinker_metrics(thinker_id=uuid.uuid4())
        assert isinstance(tm, ThinkerMetrics)

    def test_default_platform(self):
        tm = make_thinker_metrics(thinker_id=uuid.uuid4())
        assert tm.platform == "podcast"
        assert tm.followers == 0

    def test_override_platform(self):
        tm = make_thinker_metrics(thinker_id=uuid.uuid4(), platform="twitter", followers=50000)
        assert tm.platform == "twitter"
        assert tm.followers == 50000


# ---------- Source ----------


class TestMakeSource:
    def test_returns_source_instance(self):
        s = make_source()
        assert isinstance(s, Source)

    def test_has_sensible_defaults(self):
        s = make_source()
        assert s.source_type == "podcast_rss"
        assert s.approval_status == "approved"
        assert s.url is not None

    def test_unique_urls(self):
        s1 = make_source()
        s2 = make_source()
        assert s1.url != s2.url

    def test_override_source_type(self):
        s = make_source(source_type="substack")
        assert s.source_type == "substack"


# ---------- Content ----------


class TestMakeContent:
    def test_returns_content_instance(self):
        c = make_content(source_id=uuid.uuid4())
        assert isinstance(c, Content)

    def test_has_sensible_defaults(self):
        c = make_content(source_id=uuid.uuid4())
        assert c.content_type == "episode"
        assert c.status == "pending"
        assert c.canonical_url is not None
        assert c.title is not None

    def test_unique_canonical_urls(self):
        c1 = make_content(source_id=uuid.uuid4())
        c2 = make_content(source_id=uuid.uuid4())
        assert c1.canonical_url != c2.canonical_url

    def test_override_title(self):
        c = make_content(source_id=uuid.uuid4(), title="My Episode")
        assert c.title == "My Episode"


# ---------- ContentThinker ----------


class TestMakeContentThinker:
    def test_returns_instance(self):
        ct = make_content_thinker(content_id=uuid.uuid4(), thinker_id=uuid.uuid4())
        assert isinstance(ct, ContentThinker)

    def test_default_role_and_confidence(self):
        ct = make_content_thinker(content_id=uuid.uuid4(), thinker_id=uuid.uuid4())
        assert ct.role == "primary"
        assert ct.confidence == 10

    def test_override_role(self):
        ct = make_content_thinker(content_id=uuid.uuid4(), thinker_id=uuid.uuid4(), role="guest", confidence=6)
        assert ct.role == "guest"
        assert ct.confidence == 6


# ---------- CandidateThinker ----------


class TestMakeCandidateThinker:
    def test_returns_instance(self):
        ct = make_candidate_thinker()
        assert isinstance(ct, CandidateThinker)

    def test_has_sensible_defaults(self):
        ct = make_candidate_thinker()
        assert ct.name == "Test Candidate"
        assert ct.normalized_name == "test candidate"
        assert ct.status == "pending_llm"

    def test_override_name(self):
        ct = make_candidate_thinker(name="John Doe", normalized_name="john doe")
        assert ct.name == "John Doe"


# ---------- Job ----------


class TestMakeJob:
    def test_returns_instance(self):
        j = make_job()
        assert isinstance(j, Job)

    def test_has_sensible_defaults(self):
        j = make_job()
        assert j.job_type == "discover_thinker"
        assert j.status == "pending"
        assert j.priority == 5
        assert j.payload == {}

    def test_override_job_type(self):
        j = make_job(job_type="process_content", priority=3)
        assert j.job_type == "process_content"
        assert j.priority == 3


# ---------- LLMReview ----------


class TestMakeLLMReview:
    def test_returns_instance(self):
        r = make_llm_review()
        assert isinstance(r, LLMReview)

    def test_has_sensible_defaults(self):
        r = make_llm_review()
        assert r.review_type == "thinker_approval"
        assert r.trigger == "job_gate"
        assert r.context_snapshot == {}

    def test_override_review_type(self):
        r = make_llm_review(review_type="health_check", trigger="scheduled")
        assert r.review_type == "health_check"
        assert r.trigger == "scheduled"


# ---------- SystemConfig ----------


class TestMakeSystemConfig:
    def test_returns_instance(self):
        sc = make_system_config()
        assert isinstance(sc, SystemConfig)

    def test_has_sensible_defaults(self):
        sc = make_system_config()
        assert sc.key is not None
        assert sc.value == {"enabled": True}
        assert sc.set_by == "seed"

    def test_override_key_and_value(self):
        sc = make_system_config(key="workers_active", value=True, set_by="admin")
        assert sc.key == "workers_active"
        assert sc.value is True
        assert sc.set_by == "admin"


# ---------- RateLimitUsage ----------


class TestMakeRateLimitUsage:
    def test_returns_instance(self):
        rl = make_rate_limit_usage()
        assert isinstance(rl, RateLimitUsage)

    def test_has_sensible_defaults(self):
        rl = make_rate_limit_usage()
        assert rl.api_name == "test_api"
        assert rl.worker_id == "worker-1"

    def test_override_api_name(self):
        rl = make_rate_limit_usage(api_name="podcastindex")
        assert rl.api_name == "podcastindex"


# ---------- ApiUsage ----------


class TestMakeApiUsage:
    def test_returns_instance(self):
        au = make_api_usage()
        assert isinstance(au, ApiUsage)

    def test_has_sensible_defaults(self):
        au = make_api_usage()
        assert au.api_name == "test_api"
        assert au.endpoint == "search"
        assert au.call_count == 1

    def test_override_fields(self):
        au = make_api_usage(api_name="youtube", endpoint="channels.list", call_count=10)
        assert au.api_name == "youtube"
        assert au.endpoint == "channels.list"
        assert au.call_count == 10


# ---------- Cross-cutting: all make_* produce unique IDs ----------


class TestAllFactoriesUniqueIDs:
    """Verify that calling any factory twice produces different IDs."""

    def test_category_unique(self):
        assert make_category().id != make_category().id

    def test_thinker_unique(self):
        assert make_thinker().id != make_thinker().id

    def test_source_unique(self):
        uuid.uuid4()
        assert make_source().id != make_source().id

    def test_content_unique(self):
        sid, _oid = uuid.uuid4(), uuid.uuid4()
        first = make_content(source_id=sid).id
        second = make_content(source_id=sid).id
        assert first != second

    def test_job_unique(self):
        assert make_job().id != make_job().id

    def test_llm_review_unique(self):
        assert make_llm_review().id != make_llm_review().id

    def test_rate_limit_unique(self):
        assert make_rate_limit_usage().id != make_rate_limit_usage().id

    def test_api_usage_unique(self):
        assert make_api_usage().id != make_api_usage().id

    def test_candidate_thinker_unique(self):
        assert make_candidate_thinker().id != make_candidate_thinker().id

    def test_thinker_profile_unique(self):
        tid = uuid.uuid4()
        assert make_thinker_profile(thinker_id=tid).id != make_thinker_profile(thinker_id=tid).id

    def test_thinker_metrics_unique(self):
        tid = uuid.uuid4()
        assert make_thinker_metrics(thinker_id=tid).id != make_thinker_metrics(thinker_id=tid).id
