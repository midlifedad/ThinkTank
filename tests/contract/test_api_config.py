"""Contract tests for config API endpoints.

Verifies:
- GET /api/config returns list of all config entries
- GET /api/config/{key} returns specific entry or 404
- PUT /api/config/{key} upserts config entry
"""

import pytest
from httpx import AsyncClient

from tests.factories import create_system_config

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.skip(
        reason="asyncpg InterfaceError under client+session fixture interleaving — tracked in followup chore",
    ),
]


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

    async def test_put_config_creates_entry(self, client: AsyncClient, authed_admin_headers):
        """PUT /api/config/{key} creates a new config entry (auth required)."""
        payload = {"value": {"rate": 100}, "set_by": "test"}
        resp = await client.put(
            "/api/config/new_config_key",
            json=payload,
            headers=authed_admin_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["key"] == "new_config_key"
        assert body["value"] == {"rate": 100}
        assert body["set_by"] == "test"

    async def test_put_config_updates_existing(self, client: AsyncClient, session, authed_admin_headers):
        """PUT /api/config/{key} updates an existing config entry (auth required)."""
        await create_system_config(session, key="update_key", value={"old": True}, set_by="seed")
        await session.commit()

        payload = {"value": {"new": True}, "set_by": "admin"}
        resp = await client.put(
            "/api/config/update_key",
            json=payload,
            headers=authed_admin_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["value"] == {"new": True}
        assert body["set_by"] == "admin"


class TestConfigPutRequiresAuth:
    """ADMIN-REVIEW CR-02 follow-up: PUT /api/config/{key} must require admin auth.

    The list/get endpoints exclude secret_* keys, but until the PUT endpoint
    also requires auth, any anonymous caller could write `secret_anthropic_api_key`
    (exfiltrating via a chosen key, bricking the stack, or silently rotating
    a live credential to a value they control).
    """

    async def test_put_rejects_anonymous(self, client: AsyncClient, seeded_admin_token):
        """PUT /api/config/{key} without a bearer token → 401.

        The admin token IS configured (via ``seeded_admin_token``) so
        that we reach the "missing credentials" branch rather than the
        fail-closed "not configured" branch (which would return 500).
        """
        payload = {"value": {"anything": True}, "set_by": "anon"}
        resp = await client.put("/api/config/some_key", json=payload)
        assert resp.status_code == 401

    async def test_put_rejects_wrong_token(self, client: AsyncClient, seeded_admin_token):
        """PUT /api/config/{key} with a wrong bearer token → 401."""
        payload = {"value": {"x": 1}, "set_by": "anon"}
        resp = await client.put(
            "/api/config/some_key",
            json=payload,
            headers={"Authorization": "Bearer not-the-real-token"},
        )
        assert resp.status_code == 401

    async def test_put_accepts_valid_token(self, client: AsyncClient, authed_admin_headers):
        """PUT /api/config/{key} with a valid bearer token succeeds."""
        payload = {"value": {"ok": True}, "set_by": "admin"}
        resp = await client.put(
            "/api/config/gated_key",
            json=payload,
            headers=authed_admin_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["value"] == {"ok": True}

    async def test_put_fails_closed_without_admin_token_configured(self, client: AsyncClient):
        """PUT returns 500 (not 200) if no admin token is configured at all.

        Defence-in-depth: a deployment that forgets to seed
        ``secret_admin_api_token`` must stay locked, not silently accept
        anonymous writes.
        """
        payload = {"value": {"x": 1}, "set_by": "anon"}
        resp = await client.put("/api/config/whatever", json=payload)
        assert resp.status_code == 500


class TestConfigSecretRedaction:
    """ADMIN-REVIEW CR-02: secret_* keys must not be exposed via /api/config.

    Admin stores API keys (Anthropic, Railway, PodcastIndex, etc.) in
    system_config under keys prefixed with `secret_`. These must never be
    returned by the public /api/config endpoints.
    """

    async def test_list_config_excludes_secret_keys(self, client: AsyncClient, session):
        """GET /api/config omits every row whose key starts with 'secret_'."""
        await create_system_config(session, key="workers_active", value=True, set_by="seed")
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

    async def test_get_config_rejects_secret_key_even_when_missing(self, client: AsyncClient):
        """GET /api/config/secret_* returns 403 even for nonexistent secret keys.

        Prevents using 404 vs 403 response codes as an oracle for which
        secrets are set.
        """
        resp = await client.get("/api/config/secret_does_not_exist")
        assert resp.status_code == 403
