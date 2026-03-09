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
