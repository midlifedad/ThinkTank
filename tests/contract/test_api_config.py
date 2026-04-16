"""Contract tests for config API endpoints.

Verifies:
- GET /api/config returns list of all config entries
- GET /api/config/{key} returns specific entry or 404
- PUT /api/config/{key} upserts config entry
"""

import pytest
from httpx import AsyncClient

from tests.factories import create_system_config

pytestmark = pytest.mark.anyio


class TestConfigEndpointContract:
    """Contract tests for /api/config endpoints."""

    async def test_list_config_returns_list(self, client: AsyncClient, session):
        """GET /api/config returns 200 with list of config entries."""
        await create_system_config(session, key="test_key_1", value={"enabled": True})
        await session.commit()

        resp = await client.get("/api/config")
        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body, list)
        assert len(body) >= 1

    async def test_list_config_item_shape(self, client: AsyncClient, session):
        """Each config item has required fields."""
        await create_system_config(session, key="shape_key", value={"x": 1})
        await session.commit()

        resp = await client.get("/api/config")
        assert resp.status_code == 200
        item = resp.json()[0]
        required_keys = {"key", "value", "set_by", "updated_at"}
        assert required_keys.issubset(set(item.keys()))

    async def test_get_config_by_key(self, client: AsyncClient, session):
        """GET /api/config/{key} returns the config entry."""
        await create_system_config(session, key="my_key", value={"flag": True}, set_by="admin")
        await session.commit()

        resp = await client.get("/api/config/my_key")
        assert resp.status_code == 200
        body = resp.json()
        assert body["key"] == "my_key"
        assert body["value"] == {"flag": True}

    async def test_get_config_not_found(self, client: AsyncClient):
        """GET /api/config/{key} returns 404 for unknown key."""
        resp = await client.get("/api/config/nonexistent_key")
        assert resp.status_code == 404
        assert "detail" in resp.json()

    async def test_put_config_creates_entry(self, client: AsyncClient):
        """PUT /api/config/{key} creates a new config entry."""
        payload = {"value": {"rate": 100}, "set_by": "test"}
        resp = await client.put("/api/config/new_config_key", json=payload)
        assert resp.status_code == 200
        body = resp.json()
        assert body["key"] == "new_config_key"
        assert body["value"] == {"rate": 100}
        assert body["set_by"] == "test"

    async def test_put_config_updates_existing(self, client: AsyncClient, session):
        """PUT /api/config/{key} updates an existing config entry."""
        await create_system_config(session, key="update_key", value={"old": True}, set_by="seed")
        await session.commit()

        payload = {"value": {"new": True}, "set_by": "admin"}
        resp = await client.put("/api/config/update_key", json=payload)
        assert resp.status_code == 200
        body = resp.json()
        assert body["value"] == {"new": True}
        assert body["set_by"] == "admin"


class TestConfigSecretRedaction:
    """ADMIN-REVIEW CR-02: secret_* keys must not be exposed via /api/config.

    Admin stores API keys (Anthropic, Railway, PodcastIndex, etc.) in
    system_config under keys prefixed with `secret_`. These must never be
    returned by the public /api/config endpoints.
    """

    async def test_list_config_excludes_secret_keys(self, client: AsyncClient, session):
        """GET /api/config omits every row whose key starts with 'secret_'."""
        await create_system_config(
            session, key="workers_active", value=True, set_by="seed"
        )
        await create_system_config(
            session,
            key="secret_anthropic_api_key",
            value="sk-ant-redacted",
            set_by="seed",
        )
        await create_system_config(
            session,
            key="secret_railway_api_key",
            value="rw-redacted",
            set_by="seed",
        )
        await session.commit()

        resp = await client.get("/api/config")
        assert resp.status_code == 200
        body = resp.json()
        keys = [c["key"] for c in body]
        assert "workers_active" in keys
        assert "secret_anthropic_api_key" not in keys
        assert "secret_railway_api_key" not in keys
        # Hard guarantee: no secret value appears anywhere in the response text
        # (defense against accidental reintroduction via a different code path).
        body_text = resp.text
        assert "sk-ant-redacted" not in body_text
        assert "rw-redacted" not in body_text

    async def test_get_config_rejects_secret_key(self, client: AsyncClient, session):
        """GET /api/config/secret_* returns 403 regardless of whether the row exists."""
        await create_system_config(
            session,
            key="secret_railway_api_key",
            value="rw-redacted",
            set_by="seed",
        )
        await session.commit()

        resp = await client.get("/api/config/secret_railway_api_key")
        assert resp.status_code == 403
        assert "rw-redacted" not in resp.text

    async def test_get_config_rejects_secret_key_even_when_missing(
        self, client: AsyncClient
    ):
        """GET /api/config/secret_* returns 403 even for nonexistent secret keys.

        Prevents using 404 vs 403 response codes as an oracle for which
        secrets are set.
        """
        resp = await client.get("/api/config/secret_does_not_exist")
        assert resp.status_code == 403
