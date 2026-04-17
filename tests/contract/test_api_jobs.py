"""Contract tests for jobs API endpoints.

Verifies:
- GET /api/jobs/status returns 200 with correct shape
- Response includes by_type, by_status, and recent_errors
"""

import pytest
from httpx import AsyncClient

from tests.factories import create_job

pytestmark = pytest.mark.anyio


class TestJobsEndpointContract:
    """Contract tests for /api/jobs endpoints."""

    async def test_job_status_returns_correct_shape(self, client: AsyncClient, session):
        """GET /api/jobs/status returns 200 with by_type, by_status, recent_errors."""
        await create_job(session, job_type="discover_thinker", status="pending")
        await create_job(session, job_type="discover_thinker", status="done")
        await create_job(
            session,
            job_type="fetch_podcast_feed",
            status="failed",
            error="Timeout",
            error_category="network",
        )
        await session.commit()

        resp = await client.get("/api/jobs/status")
        assert resp.status_code == 200
        body = resp.json()
        assert "by_type" in body
        assert "by_status" in body
        assert "recent_errors" in body
        assert isinstance(body["by_type"], dict)
        assert isinstance(body["by_status"], dict)
        assert isinstance(body["recent_errors"], list)

    async def test_job_status_counts_correct(self, client: AsyncClient, session):
        """Job counts are grouped correctly."""
        await create_job(session, job_type="discover_thinker", status="pending")
        await create_job(session, job_type="discover_thinker", status="pending")
        await create_job(session, job_type="fetch_podcast_feed", status="done")
        await session.commit()

        resp = await client.get("/api/jobs/status")
        body = resp.json()
        assert body["by_status"].get("pending", 0) >= 2
        assert body["by_status"].get("done", 0) >= 1

    async def test_job_status_recent_errors_shape(self, client: AsyncClient, session):
        """Recent errors include error, error_category, and job_type."""
        await create_job(
            session,
            job_type="fetch_podcast_feed",
            status="failed",
            error="Connection timeout",
            error_category="network",
        )
        await session.commit()

        resp = await client.get("/api/jobs/status")
        body = resp.json()
        errors = body["recent_errors"]
        assert len(errors) >= 1
        err = errors[0]
        assert "error" in err
        assert "error_category" in err
        assert "job_type" in err
