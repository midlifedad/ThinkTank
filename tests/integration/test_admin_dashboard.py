"""Integration tests for admin dashboard and HTMX partial endpoints."""

import os

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.factories import (
    create_job,
    create_llm_review,
    create_source,
    create_system_config,
    create_thinker,
)

pytestmark = pytest.mark.anyio

TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://thinktank_test:thinktank_test@localhost:5433/thinktank_test",
)


@pytest.fixture
async def admin_client() -> AsyncClient:
    """HTTP client for admin integration tests."""
    os.environ["DATABASE_URL"] = TEST_DATABASE_URL

    from thinktank.config import get_settings

    get_settings.cache_clear()

    from thinktank.admin.main import app as admin_app

    transport = ASGITransport(app=admin_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    get_settings.cache_clear()


class TestDashboardPage:
    """Test the main dashboard page."""

    async def test_dashboard_returns_200(self, admin_client):
        response = await admin_client.get("/admin/")
        assert response.status_code == 200
        assert "ThinkTank Admin" in response.text

    async def test_dashboard_contains_htmx_widgets(self, admin_client):
        response = await admin_client.get("/admin/")
        assert "hx-get" in response.text
        assert "every 10s" in response.text


class TestQueueDepthPartial:
    """Test the queue depth partial endpoint."""

    async def test_returns_200(self, admin_client):
        response = await admin_client.get("/admin/partials/queue-depth")
        assert response.status_code == 200

    async def test_empty_queue(self, admin_client):
        response = await admin_client.get("/admin/partials/queue-depth")
        assert response.status_code == 200
        assert "No jobs in queue" in response.text

    async def test_shows_job_counts(self, admin_client, session: AsyncSession):
        await create_job(session, job_type="fetch_podcast_feed", status="pending")
        await create_job(session, job_type="fetch_podcast_feed", status="pending")
        await create_job(session, job_type="fetch_podcast_feed", status="running")
        await create_job(session, job_type="process_content", status="failed", error="test error")
        await session.commit()

        response = await admin_client.get("/admin/partials/queue-depth")
        assert response.status_code == 200
        assert "fetch_podcast_feed" in response.text
        assert "process_content" in response.text


class TestErrorLogPartial:
    """Test the error log partial endpoint."""

    async def test_returns_200(self, admin_client):
        response = await admin_client.get("/admin/partials/error-log")
        assert response.status_code == 200

    async def test_shows_failed_jobs(self, admin_client, session: AsyncSession):
        await create_job(
            session,
            job_type="fetch_podcast_feed",
            status="failed",
            error="Connection timeout to RSS feed",
            error_category="network",
        )
        await session.commit()

        response = await admin_client.get("/admin/partials/error-log")
        assert response.status_code == 200
        assert "Connection timeout" in response.text
        assert "network" in response.text

    async def test_empty_errors(self, admin_client):
        response = await admin_client.get("/admin/partials/error-log")
        assert "No recent errors" in response.text


class TestSourceHealthPartial:
    """Test the source health partial endpoint."""

    async def test_returns_200(self, admin_client):
        response = await admin_client.get("/admin/partials/source-health")
        assert response.status_code == 200

    async def test_shows_source_counts(self, admin_client, session: AsyncSession):
        thinker = await create_thinker(session)
        await create_source(session, thinker_id=thinker.id, approval_status="approved", active=True)
        await create_source(session, thinker_id=thinker.id, approval_status="approved", error_count=5, active=True)
        await create_source(session, thinker_id=thinker.id, approval_status="pending_llm", active=False)
        await session.commit()

        response = await admin_client.get("/admin/partials/source-health")
        assert response.status_code == 200
        # Should show totals
        text_content = response.text
        assert "3" in text_content  # total
        assert "1" in text_content  # errored or inactive


class TestGPUStatusPartial:
    """Test the GPU status partial endpoint."""

    async def test_returns_200(self, admin_client):
        response = await admin_client.get("/admin/partials/gpu-status")
        assert response.status_code == 200

    async def test_unknown_when_no_config(self, admin_client):
        response = await admin_client.get("/admin/partials/gpu-status")
        assert "UNKNOWN" in response.text


class TestRateLimitsPartial:
    """Test the rate limits partial endpoint."""

    async def test_returns_200(self, admin_client):
        response = await admin_client.get("/admin/partials/rate-limits")
        assert response.status_code == 200

    async def test_shows_api_gauges(self, admin_client):
        response = await admin_client.get("/admin/partials/rate-limits")
        assert response.status_code == 200
        assert "youtube" in response.text
        assert "anthropic" in response.text


class TestCostTrackerPartial:
    """Test the cost tracker partial endpoint."""

    async def test_returns_200(self, admin_client):
        response = await admin_client.get("/admin/partials/cost-tracker")
        assert response.status_code == 200

    async def test_empty_costs(self, admin_client):
        response = await admin_client.get("/admin/partials/cost-tracker")
        assert "No API usage" in response.text


# ---------------------------------------------------------------------------
# Morning briefing endpoints (Phase 8, Plan 01)
# ---------------------------------------------------------------------------


class TestHealthSummaryPartial:
    """Test the health summary partial endpoint."""

    async def test_returns_200(self, admin_client):
        response = await admin_client.get("/admin/partials/health-summary")
        assert response.status_code == 200

    async def test_shows_active_when_workers_active(self, admin_client, session: AsyncSession):
        await create_system_config(session, key="workers_active", value=True, set_by="seed")
        await session.commit()

        response = await admin_client.get("/admin/partials/health-summary")
        assert response.status_code == 200
        assert "ACTIVE" in response.text

    async def test_shows_stopped_when_workers_inactive(self, admin_client, session: AsyncSession):
        await create_system_config(session, key="workers_active", value=False, set_by="seed")
        await session.commit()

        response = await admin_client.get("/admin/partials/health-summary")
        assert response.status_code == 200
        assert "STOPPED" in response.text

    async def test_shows_db_ok(self, admin_client):
        response = await admin_client.get("/admin/partials/health-summary")
        assert response.status_code == 200
        assert "OK" in response.text


class TestKillSwitchPartial:
    """Test the kill switch partial endpoint."""

    async def test_returns_200(self, admin_client):
        response = await admin_client.get("/admin/partials/kill-switch")
        assert response.status_code == 200

    async def test_shows_toggle_button(self, admin_client):
        response = await admin_client.get("/admin/partials/kill-switch")
        assert response.status_code == 200
        assert "hx-post" in response.text
        assert "kill-switch/toggle" in response.text

    async def test_toggle_flips_value(self, admin_client, session: AsyncSession):
        await create_system_config(session, key="workers_active", value=True, set_by="seed")
        await session.commit()

        response = await admin_client.post("/admin/kill-switch/toggle")
        assert response.status_code == 200
        assert "STOPPED" in response.text

    async def test_toggle_roundtrip(self, admin_client, session: AsyncSession):
        await create_system_config(session, key="workers_active", value=True, set_by="seed")
        await session.commit()

        # First toggle: active -> stopped
        response = await admin_client.post("/admin/kill-switch/toggle")
        assert "STOPPED" in response.text

        # Second toggle: stopped -> active
        response = await admin_client.post("/admin/kill-switch/toggle")
        assert "ACTIVE" in response.text

    async def test_toggle_creates_config_when_missing(self, admin_client):
        response = await admin_client.post("/admin/kill-switch/toggle")
        assert response.status_code == 200
        # Should create with value=False (turning off)
        assert "STOPPED" in response.text


class TestActivityFeedPartial:
    """Test the activity feed partial endpoint."""

    async def test_returns_200(self, admin_client):
        response = await admin_client.get("/admin/partials/activity-feed")
        assert response.status_code == 200

    async def test_empty_feed(self, admin_client):
        response = await admin_client.get("/admin/partials/activity-feed")
        assert "No recent activity" in response.text

    async def test_shows_completed_jobs(self, admin_client, session: AsyncSession):
        from datetime import UTC, datetime

        await create_job(
            session,
            job_type="fetch_podcast_feed",
            status="complete",
            completed_at=datetime.now(UTC),
        )
        await session.commit()

        response = await admin_client.get("/admin/partials/activity-feed")
        assert response.status_code == 200
        assert "fetch_podcast_feed" in response.text


class TestPendingApprovalsPartial:
    """Test the pending approvals partial endpoint."""

    async def test_returns_200(self, admin_client):
        response = await admin_client.get("/admin/partials/pending-approvals")
        assert response.status_code == 200

    async def test_shows_zero_when_no_pending(self, admin_client):
        response = await admin_client.get("/admin/partials/pending-approvals")
        assert response.status_code == 200
        assert "0" in response.text

    async def test_shows_pending_count(self, admin_client, session: AsyncSession):
        # Create reviews with no decision (pending)
        await create_llm_review(session, decision=None)
        await create_llm_review(session, decision=None)
        # Create one with a decision (not pending)
        await create_llm_review(session, decision="approve")
        await session.commit()

        response = await admin_client.get("/admin/partials/pending-approvals")
        assert response.status_code == 200
        assert "2" in response.text

    async def test_links_to_llm_panel(self, admin_client, session: AsyncSession):
        await create_llm_review(session, decision=None)
        await session.commit()

        response = await admin_client.get("/admin/partials/pending-approvals")
        assert "/admin/llm" in response.text


class TestDashboardMorningBriefing:
    """Test the reorganized dashboard page."""

    async def test_dashboard_returns_200(self, admin_client):
        response = await admin_client.get("/admin/")
        assert response.status_code == 200

    async def test_morning_briefing_title(self, admin_client):
        response = await admin_client.get("/admin/")
        assert "Morning Briefing" in response.text

    async def test_contains_all_htmx_partials(self, admin_client):
        response = await admin_client.get("/admin/")
        text = response.text
        # New partials
        assert "partials/health-summary" in text
        assert "partials/kill-switch" in text
        assert "partials/pending-approvals" in text
        assert "partials/activity-feed" in text
        # Existing partials preserved
        assert "partials/queue-depth" in text
        assert "partials/error-log" in text
        assert "partials/source-health" in text
        assert "partials/gpu-status" in text
        assert "partials/rate-limits" in text
        assert "partials/cost-tracker" in text

    async def test_kill_switch_container_exists(self, admin_client):
        response = await admin_client.get("/admin/")
        assert 'id="kill-switch-container"' in response.text

    async def test_all_partials_autorefresh(self, admin_client):
        response = await admin_client.get("/admin/")
        # Count occurrences of every 10s to ensure all widgets refresh
        assert response.text.count("every 10s") >= 10
