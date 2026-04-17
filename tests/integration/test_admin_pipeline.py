"""Integration tests for pipeline control page: job list, filters, pagination,
manual triggers, retry, cancel, and job detail view."""

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.factories import create_job
from thinktank.models.job import Job

pytestmark = pytest.mark.anyio

TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://thinktank_test:thinktank_test@localhost:5433/thinktank_test",
)


def _now() -> datetime:
    """Timezone-aware UTC now (TIMESTAMPTZ)."""
    return datetime.now(UTC)


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


class TestPipelinePage:
    """Test the pipeline page loads correctly."""

    async def test_pipeline_page_loads(self, admin_client):
        """GET /admin/pipeline/ returns 200 with Pipeline Control heading."""
        response = await admin_client.get("/admin/pipeline/")
        assert response.status_code == 200
        assert "Pipeline Control" in response.text
        assert "Manual Triggers" in response.text

    async def test_pipeline_nav_link(self, admin_client):
        """GET /admin/ (dashboard) response contains href to pipeline page."""
        response = await admin_client.get("/admin/")
        assert response.status_code == 200
        assert 'href="/admin/pipeline"' in response.text


class TestJobList:
    """Test the job list partial endpoint."""

    async def test_job_list_partial_loads(self, admin_client):
        """GET /admin/pipeline/partials/jobs returns 200."""
        response = await admin_client.get("/admin/pipeline/partials/jobs")
        assert response.status_code == 200

    async def test_job_list_shows_jobs(self, admin_client, session: AsyncSession):
        """Seeded jobs appear in the job list partial."""
        await create_job(session, job_type="fetch_podcast_feed", status="pending")
        await create_job(session, job_type="process_content", status="running")
        await create_job(session, job_type="llm_approval_check", status="done")
        await session.commit()

        response = await admin_client.get("/admin/pipeline/partials/jobs")
        assert response.status_code == 200
        assert "fetch_podcast_feed" in response.text
        assert "process_content" in response.text
        assert "llm_approval_check" in response.text

    async def test_job_list_filter_by_status(self, admin_client, session: AsyncSession):
        """Filtering by status shows only matching jobs."""
        await create_job(session, job_type="fetch_podcast_feed", status="pending")
        await create_job(session, job_type="process_content", status="failed", error="Connection timeout")
        await session.commit()

        response = await admin_client.get("/admin/pipeline/partials/jobs?status=failed")
        assert response.status_code == 200
        assert "process_content" in response.text
        # The pending job should not appear (only failed requested)
        assert "1 job" in response.text

    async def test_job_list_filter_by_type(self, admin_client, session: AsyncSession):
        """Filtering by job_type shows only that type."""
        await create_job(session, job_type="fetch_podcast_feed", status="pending")
        await create_job(session, job_type="process_content", status="pending")
        await session.commit()

        response = await admin_client.get("/admin/pipeline/partials/jobs?job_type=fetch_podcast_feed")
        assert response.status_code == 200
        assert "fetch_podcast_feed" in response.text
        assert "1 job" in response.text

    async def test_job_list_pagination(self, admin_client, session: AsyncSession):
        """30 jobs produces 2 pages: page 1 has 25 rows, page 2 has 5."""
        for _ in range(30):
            await create_job(session, job_type="fetch_podcast_feed", status="pending")
        await session.commit()

        # Page 1
        response = await admin_client.get("/admin/pipeline/partials/jobs?page=1")
        assert response.status_code == 200
        assert "Page 1 of 2" in response.text
        assert "30 jobs" in response.text

        # Page 2
        response = await admin_client.get("/admin/pipeline/partials/jobs?page=2")
        assert response.status_code == 200
        assert "Page 2 of 2" in response.text

    async def test_job_list_filter_by_date(self, admin_client, session: AsyncSession):
        """Date range filter shows only jobs within the range."""
        old_date = _now() - timedelta(days=10)
        recent_date = _now() - timedelta(hours=2)

        await create_job(session, job_type="fetch_podcast_feed", status="done", created_at=old_date)
        await create_job(session, job_type="process_content", status="done", created_at=recent_date)
        await session.commit()

        # Filter to only today
        today_str = _now().strftime("%Y-%m-%d")
        response = await admin_client.get(f"/admin/pipeline/partials/jobs?date_from={today_str}&date_to={today_str}")
        assert response.status_code == 200
        assert "process_content" in response.text
        assert "1 job" in response.text

    async def test_job_list_empty(self, admin_client):
        """No jobs returns 200 with empty-state message."""
        response = await admin_client.get("/admin/pipeline/partials/jobs")
        assert response.status_code == 200
        assert "No jobs found" in response.text


class TestManualTrigger:
    """Test manual job trigger endpoints."""

    async def test_trigger_refresh_due_sources(self, admin_client, session: AsyncSession):
        """POST trigger creates a refresh_due_sources job."""
        response = await admin_client.post("/admin/pipeline/trigger/refresh_due_sources")
        assert response.status_code == 200
        assert "queued successfully" in response.text

        # Verify job in DB
        result = await session.execute(
            select(Job).where(Job.job_type == "refresh_due_sources").execution_options(populate_existing=True)
        )
        job = result.scalar_one()
        assert job.status == "pending"
        assert job.payload["triggered_by"] == "admin"

    async def test_trigger_scan_for_candidates(self, admin_client, session: AsyncSession):
        """POST trigger creates a scan_for_candidates job."""
        response = await admin_client.post("/admin/pipeline/trigger/scan_for_candidates")
        assert response.status_code == 200
        assert "queued successfully" in response.text

        result = await session.execute(
            select(Job).where(Job.job_type == "scan_for_candidates").execution_options(populate_existing=True)
        )
        job = result.scalar_one()
        assert job.status == "pending"

    async def test_trigger_discover_guests(self, admin_client, session: AsyncSession):
        """POST trigger with thinker_id creates discover_guests_podcastindex job with thinker_id in payload."""
        thinker_uuid = str(uuid.uuid4())
        response = await admin_client.post(
            "/admin/pipeline/trigger/discover_guests_podcastindex",
            data={"thinker_id": thinker_uuid},
        )
        assert response.status_code == 200
        assert "queued successfully" in response.text

        result = await session.execute(
            select(Job).where(Job.job_type == "discover_guests_podcastindex").execution_options(populate_existing=True)
        )
        job = result.scalar_one()
        assert job.status == "pending"
        assert job.payload["thinker_id"] == thinker_uuid
        assert job.payload["triggered_by"] == "admin"

    async def test_trigger_invalid_type(self, admin_client):
        """POST trigger with invalid job type returns 422."""
        response = await admin_client.post("/admin/pipeline/trigger/invalid_type")
        assert response.status_code == 422


class TestJobRetry:
    """Test job retry endpoint."""

    async def test_retry_failed_job(self, admin_client, session: AsyncSession):
        """Retrying a failed job creates a new pending job with same type and payload."""
        original = await create_job(
            session,
            job_type="fetch_podcast_feed",
            status="failed",
            payload={"source_id": "abc123"},
            error="Connection refused",
        )
        await session.commit()

        response = await admin_client.post(f"/admin/pipeline/jobs/{original.id}/retry")
        assert response.status_code == 200
        assert "Retry job created" in response.text

        # Verify new job exists
        result = await session.execute(
            select(Job)
            .where(Job.status == "pending", Job.job_type == "fetch_podcast_feed")
            .execution_options(populate_existing=True)
        )
        new_job = result.scalar_one()
        assert new_job.id != original.id
        assert new_job.payload["source_id"] == "abc123"

        # Original unchanged
        result = await session.execute(
            select(Job).where(Job.id == original.id).execution_options(populate_existing=True)
        )
        orig = result.scalar_one()
        assert orig.status == "failed"

    async def test_retry_non_failed_rejects(self, admin_client, session: AsyncSession):
        """Cannot retry a non-failed job."""
        job = await create_job(session, job_type="fetch_podcast_feed", status="pending")
        await session.commit()

        response = await admin_client.post(f"/admin/pipeline/jobs/{job.id}/retry")
        assert response.status_code == 200
        assert "Cannot retry" in response.text


class TestJobCancel:
    """Test job cancel endpoint."""

    async def test_cancel_pending_job(self, admin_client, session: AsyncSession):
        """Cancelling a pending job sets status to cancelled."""
        job = await create_job(session, job_type="fetch_podcast_feed", status="pending")
        await session.commit()

        response = await admin_client.post(f"/admin/pipeline/jobs/{job.id}/cancel")
        assert response.status_code == 200
        assert "cancelled" in response.text

        # Verify status changed
        result = await session.execute(select(Job).where(Job.id == job.id).execution_options(populate_existing=True))
        updated = result.scalar_one()
        assert updated.status == "cancelled"

    async def test_cancel_non_pending_rejects(self, admin_client, session: AsyncSession):
        """Cannot cancel a non-pending job."""
        job = await create_job(session, job_type="fetch_podcast_feed", status="running")
        await session.commit()

        response = await admin_client.post(f"/admin/pipeline/jobs/{job.id}/cancel")
        assert response.status_code == 200
        assert "Cannot cancel" in response.text


class TestJobDetail:
    """Test job detail endpoint."""

    async def test_job_detail_loads(self, admin_client, session: AsyncSession):
        """Job detail shows all fields for a seeded job."""
        job = await create_job(
            session,
            job_type="fetch_podcast_feed",
            status="done",
            payload={"key": "value"},
            priority=3,
        )
        await session.commit()

        response = await admin_client.get(f"/admin/pipeline/jobs/{job.id}")
        assert response.status_code == 200
        assert "fetch_podcast_feed" in response.text
        # Jinja2 HTML-escapes quotes to &#34; in <pre> blocks
        assert "key" in response.text
        assert "value" in response.text

    async def test_job_detail_404(self, admin_client):
        """GET detail for nonexistent job returns 404."""
        random_id = uuid.uuid4()
        response = await admin_client.get(f"/admin/pipeline/jobs/{random_id}")
        assert response.status_code == 404

    async def test_job_detail_shows_error(self, admin_client, session: AsyncSession):
        """Failed job detail shows the error message."""
        job = await create_job(
            session,
            job_type="process_content",
            status="failed",
            error="Transcription service unavailable",
            error_category="external_api",
        )
        await session.commit()

        response = await admin_client.get(f"/admin/pipeline/jobs/{job.id}")
        assert response.status_code == 200
        assert "Transcription service unavailable" in response.text
        assert "external_api" in response.text
