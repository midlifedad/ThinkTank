"""Integration tests for recurring task scheduler editor endpoints."""

import os
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.factories import create_system_config
from thinktank.models.config_table import SystemConfig
from thinktank.models.job import Job

pytestmark = pytest.mark.anyio

TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL", "postgresql+asyncpg://thinktank_test:thinktank_test@localhost:5433/thinktank_test"
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


class TestSchedulerPartial:
    """Test the scheduler editor partial endpoint."""

    async def test_scheduler_partial_loads(self, admin_client):
        """GET /admin/pipeline/partials/scheduler returns 200 with all 5 task labels."""
        response = await admin_client.get("/admin/pipeline/partials/scheduler")
        assert response.status_code == 200
        assert "Refresh Due Sources" in response.text
        assert "Scan for Candidates" in response.text
        assert "LLM Health Check" in response.text
        assert "LLM Daily Digest" in response.text
        assert "LLM Weekly Audit" in response.text

    async def test_scheduler_shows_defaults(self, admin_client):
        """With no system_config rows, partial shows default frequencies."""
        response = await admin_client.get("/admin/pipeline/partials/scheduler")
        assert response.status_code == 200
        # Default frequencies: 1, 24, 6, 24, 168
        assert 'value="1"' in response.text
        assert 'value="6"' in response.text
        assert 'value="168"' in response.text

    async def test_scheduler_shows_custom_config(self, admin_client, session: AsyncSession):
        """Seeded system_config row overrides the default frequency."""
        await create_system_config(
            session,
            key="scheduler_refresh_due_sources",
            value={
                "frequency_hours": 4,
                "enabled": True,
                "last_run_at": None,
                "next_run_at": None,
            },
            set_by="test",
        )
        await session.commit()

        response = await admin_client.get("/admin/pipeline/partials/scheduler")
        assert response.status_code == 200
        assert 'value="4"' in response.text


class TestSchedulerSave:
    """Test the scheduler save endpoint."""

    async def test_save_frequency(self, admin_client, session: AsyncSession):
        """POST save with frequency_hours=4 persists to system_config."""
        response = await admin_client.post(
            "/admin/pipeline/scheduler/refresh_due_sources/save", data={"frequency_hours": "4"}
        )
        assert response.status_code == 200
        assert "saved" in response.text.lower()

        # Verify DB
        result = await session.execute(
            select(SystemConfig.value).where(SystemConfig.key == "scheduler_refresh_due_sources")
        )
        saved = result.scalar_one_or_none()
        assert saved is not None
        assert saved["frequency_hours"] == 4

    async def test_save_frequency_creates_config(self, admin_client, session: AsyncSession):
        """No existing row -- POST save creates new system_config row."""
        response = await admin_client.post(
            "/admin/pipeline/scheduler/scan_for_candidates/save", data={"frequency_hours": "12"}
        )
        assert response.status_code == 200

        result = await session.execute(
            select(SystemConfig.value).where(SystemConfig.key == "scheduler_scan_for_candidates")
        )
        saved = result.scalar_one_or_none()
        assert saved is not None
        assert saved["frequency_hours"] == 12
        assert saved["enabled"] is True

    async def test_save_frequency_updates_existing(self, admin_client, session: AsyncSession):
        """Seed existing row with frequency_hours=6, POST save with 2, verify updated."""
        await create_system_config(
            session,
            key="scheduler_refresh_due_sources",
            value={
                "frequency_hours": 6,
                "enabled": True,
                "last_run_at": None,
                "next_run_at": None,
            },
            set_by="test",
        )
        await session.commit()

        response = await admin_client.post(
            "/admin/pipeline/scheduler/refresh_due_sources/save", data={"frequency_hours": "2"}
        )
        assert response.status_code == 200

        result = await session.execute(
            select(SystemConfig.value)
            .where(SystemConfig.key == "scheduler_refresh_due_sources")
            .execution_options(populate_existing=True)
        )
        saved = result.scalar_one_or_none()
        assert saved is not None
        assert saved["frequency_hours"] == 2

    async def test_save_invalid_task_key(self, admin_client):
        """POST save with unknown task key returns 404."""
        response = await admin_client.post("/admin/pipeline/scheduler/nonexistent/save", data={"frequency_hours": "4"})
        assert response.status_code == 404


class TestSchedulerToggle:
    """Test the scheduler toggle endpoint."""

    async def test_toggle_enables(self, admin_client, session: AsyncSession):
        """Seed disabled task, POST toggle, verify enabled=true in DB."""
        await create_system_config(
            session,
            key="scheduler_refresh_due_sources",
            value={
                "frequency_hours": 1,
                "enabled": False,
                "last_run_at": None,
                "next_run_at": None,
            },
            set_by="test",
        )
        await session.commit()

        response = await admin_client.post("/admin/pipeline/scheduler/refresh_due_sources/toggle")
        assert response.status_code == 200

        result = await session.execute(
            select(SystemConfig.value)
            .where(SystemConfig.key == "scheduler_refresh_due_sources")
            .execution_options(populate_existing=True)
        )
        saved = result.scalar_one_or_none()
        assert saved is not None
        assert saved["enabled"] is True
        # When enabled, next_run_at should be set
        assert saved["next_run_at"] is not None

    async def test_toggle_disables(self, admin_client, session: AsyncSession):
        """Seed enabled task, POST toggle, verify enabled=false in DB."""
        await create_system_config(
            session,
            key="scheduler_scan_for_candidates",
            value={
                "frequency_hours": 24,
                "enabled": True,
                "last_run_at": None,
                "next_run_at": (_now() + timedelta(hours=24)).isoformat(),
            },
            set_by="test",
        )
        await session.commit()

        response = await admin_client.post("/admin/pipeline/scheduler/scan_for_candidates/toggle")
        assert response.status_code == 200

        result = await session.execute(
            select(SystemConfig.value)
            .where(SystemConfig.key == "scheduler_scan_for_candidates")
            .execution_options(populate_existing=True)
        )
        saved = result.scalar_one_or_none()
        assert saved is not None
        assert saved["enabled"] is False

    async def test_toggle_creates_default_if_missing(self, admin_client, session: AsyncSession):
        """No existing config -- POST toggle creates with enabled=false (toggled from default true)."""
        response = await admin_client.post("/admin/pipeline/scheduler/llm_health_check/toggle")
        assert response.status_code == 200

        result = await session.execute(
            select(SystemConfig.value).where(SystemConfig.key == "scheduler_llm_health_check")
        )
        saved = result.scalar_one_or_none()
        assert saved is not None
        assert saved["enabled"] is False
        assert saved["frequency_hours"] == 6  # default for llm_health_check


class TestSchedulerRunNow:
    """Test the scheduler run-now endpoint."""

    async def test_run_now_creates_job(self, admin_client, session: AsyncSession):
        """POST run-now for refresh_due_sources creates a Job with correct payload."""
        response = await admin_client.post("/admin/pipeline/scheduler/refresh_due_sources/run-now")
        assert response.status_code == 200
        assert "triggered" in response.text.lower()

        # Verify job in DB
        result = await session.execute(
            select(Job).where(Job.job_type == "refresh_due_sources").execution_options(populate_existing=True)
        )
        job = result.scalar_one()
        assert job.status == "pending"
        assert job.payload["triggered_by"] == "admin:scheduler_run_now"

    async def test_run_now_updates_last_run(self, admin_client, session: AsyncSession):
        """POST run-now for scan_for_candidates sets last_run_at to approximately now."""
        response = await admin_client.post("/admin/pipeline/scheduler/scan_for_candidates/run-now")
        assert response.status_code == 200

        result = await session.execute(
            select(SystemConfig.value).where(SystemConfig.key == "scheduler_scan_for_candidates")
        )
        saved = result.scalar_one_or_none()
        assert saved is not None
        assert saved["last_run_at"] is not None
        # Verify last_run_at is approximately now (within 60 seconds)
        last_run = datetime.fromisoformat(saved["last_run_at"])
        assert abs((last_run - _now()).total_seconds()) < 60

    async def test_run_now_llm_task_no_job(self, admin_client, session: AsyncSession):
        """POST run-now for llm_health_check creates NO job -- returns info message."""
        response = await admin_client.post("/admin/pipeline/scheduler/llm_health_check/run-now")
        assert response.status_code == 200
        assert "LLM tasks run on their internal schedule" in response.text

        # Verify no job created
        result = await session.execute(select(Job).execution_options(populate_existing=True))
        jobs = result.scalars().all()
        assert len(jobs) == 0

    async def test_run_now_invalid_task_key(self, admin_client):
        """POST run-now with unknown task key returns 404."""
        response = await admin_client.post("/admin/pipeline/scheduler/nonexistent/run-now")
        assert response.status_code == 404
