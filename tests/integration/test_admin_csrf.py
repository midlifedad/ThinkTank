"""Integration tests for admin CSRF middleware.

Source: ADMIN-REVIEW HI-05 — state-changing admin POSTs were vulnerable
to CSRF once CR-01 auth cookies existed. The CSRFMiddleware enforces a
double-submit cookie: the request must carry a ``csrf_token`` cookie AND
an ``X-CSRF-Token`` header whose values match.

This suite verifies:
- Safe (GET) responses set a csrf_token cookie when missing.
- State-changing admin POSTs without the CSRF header are rejected (403).
- State-changing admin POSTs with a mismatched header are rejected.
- State-changing admin POSTs with a matching header pass CSRF and reach
  the auth layer (i.e. the 401/200 outcome is driven by auth, not CSRF).
- /admin/login is exempt (clients can't have a token pre-auth).
- Non-admin paths are untouched by the middleware.
- Successful login sets the csrf_token cookie alongside admin_session.
- logout clears the csrf_token cookie.
"""

import os

import pytest
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.anyio

TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://thinktank_test:thinktank_test@localhost:5433/thinktank_test",
)


@pytest.fixture
async def admin_client() -> AsyncClient:
    """HTTP client for admin CSRF tests."""
    os.environ["DATABASE_URL"] = TEST_DATABASE_URL

    from thinktank.config import get_settings

    get_settings.cache_clear()

    from thinktank.admin.main import app as admin_app

    transport = ASGITransport(app=admin_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    get_settings.cache_clear()


class TestCSRFCookieBootstrap:
    """Safe responses must seed a csrf_token cookie when one is missing."""

    async def test_login_get_sets_csrf_cookie(self, admin_client):
        """GET /admin/login returns a csrf_token cookie (so the login form
        can post with CSRF even though login itself is exempt)."""
        resp = await admin_client.get("/admin/login")
        assert resp.status_code == 200
        assert "csrf_token=" in resp.headers.get("set-cookie", "")

    async def test_existing_csrf_cookie_not_overwritten(self, admin_client):
        """If the client already has a csrf_token, safe responses don't rotate it."""
        resp = await admin_client.get(
            "/admin/login",
            cookies={"csrf_token": "preexisting-token-value"},
        )
        assert resp.status_code == 200
        # No new csrf_token set-cookie if one was already presented.
        assert "csrf_token=" not in resp.headers.get("set-cookie", "")


class TestCSRFEnforcement:
    """State-changing admin endpoints must reject requests with missing
    or mismatched CSRF tokens."""

    async def test_post_without_csrf_rejected(self, admin_client, seeded_admin_token):
        """POST with valid auth but no CSRF header → 403."""
        resp = await admin_client.post(
            "/admin/kill-switch/toggle",
            headers={"Authorization": f"Bearer {seeded_admin_token}"},
            cookies={"csrf_token": "any-value"},
        )
        assert resp.status_code == 403
        assert "CSRF" in resp.json()["detail"]

    async def test_post_with_mismatched_csrf_rejected(self, admin_client, seeded_admin_token):
        """POST with CSRF cookie + header that do not match → 403."""
        resp = await admin_client.post(
            "/admin/kill-switch/toggle",
            headers={
                "Authorization": f"Bearer {seeded_admin_token}",
                "X-CSRF-Token": "header-value",
            },
            cookies={"csrf_token": "cookie-value"},
        )
        assert resp.status_code == 403

    async def test_post_with_matching_csrf_passes_middleware(self, admin_client, seeded_admin_token):
        """POST with matching CSRF cookie + header reaches the auth layer
        (not blocked by CSRF — the 4xx/2xx outcome is then auth-driven)."""
        token = "matching-csrf-token-xyz"
        resp = await admin_client.post(
            "/admin/kill-switch/toggle",
            headers={
                "Authorization": f"Bearer {seeded_admin_token}",
                "X-CSRF-Token": token,
            },
            cookies={"csrf_token": token},
        )
        # CSRF passed — whatever auth/handler returns is acceptable as
        # long as it's not 403 CSRF rejection.
        assert resp.status_code != 403

    async def test_post_missing_cookie_rejected(self, admin_client, seeded_admin_token):
        """Header without matching cookie → 403 (prevents attacker from
        forging the header alone)."""
        resp = await admin_client.post(
            "/admin/kill-switch/toggle",
            headers={
                "Authorization": f"Bearer {seeded_admin_token}",
                "X-CSRF-Token": "token-without-cookie",
            },
        )
        assert resp.status_code == 403


class TestCSRFExemptions:
    """Login must remain reachable without a CSRF token (pre-auth)."""

    async def test_login_post_exempt_from_csrf(self, admin_client, seeded_admin_token):
        """POST /admin/login works without any CSRF header or cookie."""
        resp = await admin_client.post(
            "/admin/login",
            data={"token": seeded_admin_token},
        )
        assert resp.status_code in (200, 303)


class TestCSRFScope:
    """Middleware only touches /admin/* paths."""

    async def test_non_admin_path_untouched(self, admin_client):
        """A POST to a non-admin path is not CSRF-checked by admin middleware.

        The admin app only serves /admin/*, so other paths 404; the test
        asserts the response is NOT a 403 CSRF rejection.
        """
        resp = await admin_client.post("/not-admin")
        assert resp.status_code != 403


class TestLoginSetsCSRFToken:
    """Successful login must deliver a csrf_token cookie so HTMX can use it."""

    async def test_login_sets_csrf_cookie(self, admin_client, seeded_admin_token):
        """POST /admin/login with correct token sets csrf_token cookie."""
        resp = await admin_client.post(
            "/admin/login",
            data={"token": seeded_admin_token},
        )
        assert resp.status_code == 200
        set_cookie = resp.headers.get("set-cookie", "")
        assert "csrf_token=" in set_cookie
        # Cookie must NOT be HttpOnly — JS must read it to echo into header.
        # The csrf_token set-cookie segment should not carry HttpOnly.
        cookies = set_cookie.split(",")
        csrf_segment = next((c for c in cookies if "csrf_token=" in c), "")
        assert "HttpOnly" not in csrf_segment


class TestLogoutClearsCSRF:
    """Logout must clear the CSRF cookie alongside session cookies."""

    async def test_logout_clears_csrf(self, admin_client, seeded_admin_token):
        """POST /admin/logout with CSRF header clears the csrf_token cookie."""
        token = "logout-csrf-token"
        resp = await admin_client.post(
            "/admin/logout",
            headers={"X-CSRF-Token": token},
            cookies={"csrf_token": token},
        )
        assert resp.status_code == 200
        set_cookie = resp.headers.get("set-cookie", "")
        # A delete_cookie call sets Max-Age=0 or an expired date for the
        # cookie name. Just check csrf_token appears in the delete flow.
        assert "csrf_token=" in set_cookie
