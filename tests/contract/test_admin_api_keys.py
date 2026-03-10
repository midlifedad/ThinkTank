"""Contract tests for admin API keys management endpoints."""

import os

import pytest
from httpx import ASGITransport, AsyncClient

from src.thinktank.models.config_table import SystemConfig

TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://thinktank_test:thinktank_test@localhost:5433/thinktank_test",
)


@pytest.fixture
async def admin_client():
    """HTTP client for the admin FastAPI app."""
    os.environ["DATABASE_URL"] = TEST_DATABASE_URL

    from thinktank.config import get_settings
    get_settings.cache_clear()

    from thinktank.admin.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    get_settings.cache_clear()


class TestApiKeysPage:
    """GET /admin/api-keys/ returns the management page."""

    async def test_returns_200(self, admin_client):
        resp = await admin_client.get("/admin/api-keys/")
        assert resp.status_code == 200
        assert "API Keys" in resp.text

    async def test_contains_form(self, admin_client):
        resp = await admin_client.get("/admin/api-keys/")
        assert 'name="key_name"' in resp.text
        assert 'name="key_value"' in resp.text


class TestApiKeysListPartial:
    """GET /admin/api-keys/partials/list returns key status table."""

    async def test_returns_200(self, admin_client):
        resp = await admin_client.get("/admin/api-keys/partials/list")
        assert resp.status_code == 200
        assert "Anthropic API Key" in resp.text

    async def test_shows_not_set_for_unconfigured(self, admin_client):
        resp = await admin_client.get("/admin/api-keys/partials/list")
        assert "Not set" in resp.text


class TestSetApiKey:
    """POST /admin/api-keys/set upserts a key in system_config."""

    async def test_set_key_returns_200(self, admin_client):
        resp = await admin_client.post(
            "/admin/api-keys/set",
            data={"key_name": "anthropic_api_key", "key_value": "sk-test-123456789"},
        )
        assert resp.status_code == 200

    async def test_set_key_shows_configured(self, admin_client):
        resp = await admin_client.post(
            "/admin/api-keys/set",
            data={"key_name": "anthropic_api_key", "key_value": "sk-test-123456789"},
        )
        assert "Configured" in resp.text
        assert "****6789" in resp.text

    async def test_set_key_persists_in_db(self, admin_client, session):
        await admin_client.post(
            "/admin/api-keys/set",
            data={"key_name": "listennotes_api_key", "key_value": "ln-key-abcdef"},
        )
        from sqlalchemy import select
        result = await session.execute(
            select(SystemConfig.value).where(
                SystemConfig.key == "secret_listennotes_api_key"
            )
        )
        value = result.scalar_one_or_none()
        assert value is not None
        assert "abcdef" in str(value)

    async def test_update_existing_key(self, admin_client):
        # Set initial value
        await admin_client.post(
            "/admin/api-keys/set",
            data={"key_name": "anthropic_api_key", "key_value": "sk-old-value1234"},
        )
        # Update to new value
        resp = await admin_client.post(
            "/admin/api-keys/set",
            data={"key_name": "anthropic_api_key", "key_value": "sk-new-value5678"},
        )
        assert resp.status_code == 200
        assert "****5678" in resp.text

    async def test_rejects_unknown_key(self, admin_client):
        resp = await admin_client.post(
            "/admin/api-keys/set",
            data={"key_name": "fake_key", "key_value": "whatever"},
        )
        assert resp.status_code == 400


class TestDeleteApiKey:
    """POST /admin/api-keys/delete/{key_name} removes a key."""

    async def test_delete_existing_key(self, admin_client):
        # Set a key first
        await admin_client.post(
            "/admin/api-keys/set",
            data={"key_name": "youtube_api_key", "key_value": "yt-key-12345678"},
        )
        # Delete it
        resp = await admin_client.post("/admin/api-keys/delete/youtube_api_key")
        assert resp.status_code == 200
        assert "Not set" in resp.text or "youtube_api_key" not in resp.text

    async def test_delete_nonexistent_key_is_ok(self, admin_client):
        resp = await admin_client.post("/admin/api-keys/delete/railway_api_key")
        assert resp.status_code == 200

    async def test_rejects_unknown_key(self, admin_client):
        resp = await admin_client.post("/admin/api-keys/delete/fake_key")
        assert resp.status_code == 400
