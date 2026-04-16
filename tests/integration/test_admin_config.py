"""Integration tests for admin config page, rate limits editor, and system config editor."""

import os

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.factories import create_system_config

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


class TestConfigPage:
    """Test the config landing page."""

    async def test_config_page_loads(self, admin_client):
        """GET /admin/config returns 200 with Rate Limits and System Settings sections."""
        response = await admin_client.get("/admin/config/")
        assert response.status_code == 200
        assert "Rate Limits" in response.text
        assert "System Settings" in response.text

    async def test_config_router_registered(self, admin_client):
        """Verify /admin/config is accessible from the admin app (regression test)."""
        response = await admin_client.get("/admin/config/")
        assert response.status_code == 200
        assert "System Configuration" in response.text


class TestRateLimitsEditor:
    """Test the rate limits editor partial and save endpoints."""

    async def test_rate_limits_partial_loads(self, admin_client):
        """GET /admin/config/partials/rate-limits returns 200 with default values."""
        response = await admin_client.get("/admin/config/partials/rate-limits")
        assert response.status_code == 200
        assert "200" in response.text  # youtube default
        assert "500" in response.text  # podcastindex default
        assert "50" in response.text   # anthropic default

    async def test_rate_limits_partial_loads_custom(
        self, admin_client, session: AsyncSession
    ):
        """Seeded custom rate_limits are shown in the partial."""
        await create_system_config(
            session,
            key="rate_limits",
            value={"youtube": 300, "podcastindex": 600, "anthropic": 100},
            set_by="test",
        )
        await session.commit()

        response = await admin_client.get("/admin/config/partials/rate-limits")
        assert response.status_code == 200
        assert "300" in response.text
        assert "600" in response.text
        assert "100" in response.text

    async def test_save_rate_limits(self, admin_client, session: AsyncSession):
        """POST /admin/config/rate-limits/save persists new limits to DB."""
        response = await admin_client.post(
            "/admin/config/rate-limits/save",
            data={
                "limit_youtube": "300",
                "limit_podcastindex": "600",
                "limit_anthropic": "100",
            },
        )
        assert response.status_code == 200
        assert "saved" in response.text.lower()

        # Verify DB
        from sqlalchemy import select

        from thinktank.models.config_table import SystemConfig

        result = await session.execute(
            select(SystemConfig.value).where(SystemConfig.key == "rate_limits")
        )
        saved = result.scalar_one_or_none()
        assert saved is not None
        assert saved["youtube"] == 300
        assert saved["podcastindex"] == 600
        assert saved["anthropic"] == 100

    async def test_save_rate_limits_updates_existing(
        self, admin_client, session: AsyncSession
    ):
        """Saving rate limits twice correctly upserts (second values win)."""
        # First save
        await admin_client.post(
            "/admin/config/rate-limits/save",
            data={
                "limit_youtube": "300",
                "limit_podcastindex": "600",
                "limit_anthropic": "100",
            },
        )

        # Second save with different values
        response = await admin_client.post(
            "/admin/config/rate-limits/save",
            data={
                "limit_youtube": "400",
                "limit_podcastindex": "700",
                "limit_anthropic": "150",
            },
        )
        assert response.status_code == 200

        # Verify second values are in DB
        from sqlalchemy import select

        from thinktank.models.config_table import SystemConfig

        result = await session.execute(
            select(SystemConfig.value).where(SystemConfig.key == "rate_limits")
        )
        saved = result.scalar_one_or_none()
        assert saved is not None
        assert saved["youtube"] == 400
        assert saved["podcastindex"] == 700
        assert saved["anthropic"] == 150


class TestSystemSettingsEditor:
    """Test the system settings editor partial and save endpoints."""

    async def test_system_settings_partial_loads(self, admin_client):
        """GET /admin/config/partials/system-settings returns 200 with default values."""
        response = await admin_client.get("/admin/config/partials/system-settings")
        assert response.status_code == 200
        assert "LLM Timeout" in response.text
        assert "Backpressure" in response.text
        assert "Stale Job" in response.text
        assert "Max Candidates" in response.text

    async def test_system_settings_partial_loads_custom(
        self, admin_client, session: AsyncSession
    ):
        """Seeded custom system config values are shown in the partial."""
        await create_system_config(
            session, key="llm_timeout_hours", value=5, set_by="test"
        )
        await create_system_config(
            session, key="backpressure_threshold", value=200, set_by="test"
        )
        await create_system_config(
            session, key="stale_job_minutes", value=60, set_by="test"
        )
        await create_system_config(
            session, key="max_candidates_per_day", value=25, set_by="test"
        )
        await session.commit()

        response = await admin_client.get("/admin/config/partials/system-settings")
        assert response.status_code == 200
        # Verify custom values appear in input fields
        assert 'value="5"' in response.text
        assert 'value="200"' in response.text
        assert 'value="60"' in response.text
        assert 'value="25"' in response.text

    async def test_save_system_settings(self, admin_client, session: AsyncSession):
        """POST /admin/config/system/save persists all 4 settings to DB."""
        response = await admin_client.post(
            "/admin/config/system/save",
            data={
                "llm_timeout_hours": "4",
                "backpressure_threshold": "150",
                "stale_job_minutes": "45",
                "max_candidates_per_day": "30",
            },
        )
        assert response.status_code == 200
        assert "saved" in response.text.lower()

        # Verify DB
        from sqlalchemy import select

        from thinktank.models.config_table import SystemConfig

        for key, expected in [
            ("llm_timeout_hours", 4),
            ("backpressure_threshold", 150),
            ("stale_job_minutes", 45),
            ("max_candidates_per_day", 30),
        ]:
            result = await session.execute(
                select(SystemConfig.value).where(SystemConfig.key == key)
            )
            saved = result.scalar_one_or_none()
            assert saved == expected, f"{key}: expected {expected}, got {saved}"

    async def test_save_system_settings_partial_update(
        self, admin_client, session: AsyncSession
    ):
        """Saving settings, then changing one value preserves all 4 correctly."""
        # First save
        await admin_client.post(
            "/admin/config/system/save",
            data={
                "llm_timeout_hours": "4",
                "backpressure_threshold": "150",
                "stale_job_minutes": "45",
                "max_candidates_per_day": "30",
            },
        )

        # Second save -- change only llm_timeout_hours
        response = await admin_client.post(
            "/admin/config/system/save",
            data={
                "llm_timeout_hours": "8",
                "backpressure_threshold": "150",
                "stale_job_minutes": "45",
                "max_candidates_per_day": "30",
            },
        )
        assert response.status_code == 200

        # Verify all 4 values
        from sqlalchemy import select

        from thinktank.models.config_table import SystemConfig

        for key, expected in [
            ("llm_timeout_hours", 8),
            ("backpressure_threshold", 150),
            ("stale_job_minutes", 45),
            ("max_candidates_per_day", 30),
        ]:
            result = await session.execute(
                select(SystemConfig.value).where(SystemConfig.key == key)
            )
            saved = result.scalar_one_or_none()
            assert saved == expected, f"{key}: expected {expected}, got {saved}"
