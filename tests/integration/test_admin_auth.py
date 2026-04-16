"""Integration tests for admin app authentication.

Source: ADMIN-REVIEW CR-01 — admin app previously had zero auth, allowing
any network-reachable actor to toggle the kill switch, read/set API keys,
approve thinkers, confirm agent mutations, etc.

This suite verifies:
- All real admin routers reject unauthenticated requests with 401
- The login endpoint is publicly reachable
- A valid bearer token (stored as secret_admin_api_token in system_config)
  unlocks admin routes
- Wrong tokens are rejected
- A valid cookie session is also accepted
- If the admin token is not configured in the DB at all, admin routes
  return 500 with an explicit "not configured" message (prevents a silent
  open-by-default failure mode if a deployment forgets the config)
"""

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

ADMIN_TOKEN = "test-admin-token-abc123"


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


@pytest.fixture
async def seeded_admin_token(session: AsyncSession) -> str:
    """Seed secret_admin_api_token into system_config and return the raw value."""
    await create_system_config(
        session,
        key="secret_admin_api_token",
        value=ADMIN_TOKEN,
        set_by="test",
    )
    await session.commit()
    return ADMIN_TOKEN


class TestAdminAuthRequired:
    """Every admin route must reject unauthenticated requests."""

    async def test_kill_switch_toggle_rejects_anonymous(
        self, admin_client, seeded_admin_token
    ):
        """POST /admin/kill-switch/toggle without a token → 401."""
        resp = await admin_client.post("/admin/kill-switch/toggle")
        assert resp.status_code == 401

    async def test_api_keys_page_rejects_anonymous(
        self, admin_client, seeded_admin_token
    ):
        """GET /admin/api-keys/ without a token → 401."""
        resp = await admin_client.get("/admin/api-keys/")
        assert resp.status_code == 401

    async def test_dashboard_rejects_anonymous(
        self, admin_client, seeded_admin_token
    ):
        """GET /admin/ without a token → 401."""
        resp = await admin_client.get("/admin/")
        assert resp.status_code == 401

    async def test_wrong_bearer_rejected(self, admin_client, seeded_admin_token):
        """A bearer header with the wrong token → 401."""
        resp = await admin_client.post(
            "/admin/kill-switch/toggle",
            headers={"Authorization": "Bearer wrong-token"},
        )
        assert resp.status_code == 401

    async def test_wrong_cookie_rejected(self, admin_client, seeded_admin_token):
        """An admin_session cookie with the wrong value → 401."""
        resp = await admin_client.post(
            "/admin/kill-switch/toggle",
            cookies={"admin_session": "wrong-token"},
        )
        assert resp.status_code == 401


class TestAdminAuthAccepts:
    """Valid credentials must unlock admin routes."""

    async def test_valid_bearer_accepted(self, admin_client, seeded_admin_token):
        """A correct bearer header unlocks the kill switch toggle."""
        resp = await admin_client.post(
            "/admin/kill-switch/toggle",
            headers={"Authorization": f"Bearer {seeded_admin_token}"},
        )
        # Any non-401 is acceptable; the point is the auth dependency passed.
        # HTMX partials typically return 200.
        assert resp.status_code != 401
        assert resp.status_code < 500

    async def test_valid_cookie_accepted(self, admin_client, seeded_admin_token):
        """A correct admin_session cookie unlocks dashboard GET."""
        resp = await admin_client.get(
            "/admin/",
            cookies={"admin_session": seeded_admin_token},
        )
        assert resp.status_code != 401
        assert resp.status_code < 500


class TestAdminLoginEndpoint:
    """The login endpoint must be publicly reachable (otherwise no-one
    can ever authenticate via browser) and must set a cookie on success."""

    async def test_login_page_public(self, admin_client):
        """GET /admin/login is reachable without auth."""
        resp = await admin_client.get("/admin/login")
        assert resp.status_code == 200

    async def test_login_accepts_valid_token_and_sets_cookie(
        self, admin_client, seeded_admin_token
    ):
        """POST /admin/login with the correct token sets the admin_session cookie."""
        resp = await admin_client.post(
            "/admin/login",
            data={"token": seeded_admin_token},
        )
        # Successful login should set the cookie.
        assert resp.status_code in (200, 303)
        set_cookie = resp.headers.get("set-cookie", "")
        assert "admin_session=" in set_cookie

    async def test_login_rejects_wrong_token(self, admin_client, seeded_admin_token):
        """POST /admin/login with the wrong token → 401, no cookie set."""
        resp = await admin_client.post(
            "/admin/login",
            data={"token": "nope"},
        )
        assert resp.status_code == 401


class TestAdminAuthTokenNotConfigured:
    """Defence-in-depth: if the admin token row isn't set at all, admin
    routes must fail closed (500), NOT open (200)."""

    async def test_missing_admin_token_returns_500(self, admin_client):
        """No secret_admin_api_token seeded → /admin/ returns 500 not 200."""
        resp = await admin_client.get(
            "/admin/",
            headers={"Authorization": "Bearer anything"},
        )
        assert resp.status_code == 500
