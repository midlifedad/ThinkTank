"""Integration test fixtures.

Applies _cleanup_tables as autouse for all integration tests
so each test gets clean database tables.

Also auto-bypasses the admin ``require_admin`` dependency for all
integration tests that aren't the admin-auth suite itself. This is
necessary because Phase 1 of the 2026-04-16 remediation plan added
auth to every admin router (ADMIN-REVIEW CR-01). Without this
override, every pre-existing admin integration test would need an
explicit bearer header.

The admin-auth suite (``test_admin_auth.py``) explicitly tests the
auth dependency and must NOT have it overridden — it opts out via a
marker check.
"""

import os

import pytest

TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://thinktank_test:thinktank_test@localhost:5433/thinktank_test",
)


@pytest.fixture(autouse=True)
async def _auto_cleanup(_cleanup_tables):
    """Auto-apply table cleanup for all integration tests."""
    yield


@pytest.fixture(autouse=True)
def _auto_admin_auth_override(request):
    """Bypass require_admin for integration tests that use admin_client.

    Pre-existing admin integration tests were written before auth
    existed; they should continue to run as if the caller were
    authenticated. The dedicated auth suite
    (``tests/integration/test_admin_auth.py``) exercises the real
    dependency and must opt out of this override.

    Only fires when the test actually uses ``admin_client``. This avoids
    importing the admin app for non-admin integration tests, which
    would force the shared ``thinktank.database.engine`` to be created
    before the client fixture can point DATABASE_URL at the test DB.

    IMPORTANT: when this autouse fires it MUST ensure DATABASE_URL is
    pointed at the test database BEFORE importing ``thinktank.admin.main``.
    Otherwise the module-level ``thinktank.database.engine`` is bound to
    whatever URL settings happen to hold at import time (potentially the
    production default), and every subsequent test that uses the shared
    session factory hits the wrong database.
    """
    # Opt out for the dedicated auth suite -- it needs the real dep.
    if "test_admin_auth" in request.node.nodeid:
        yield
        return

    if "admin_client" not in request.fixturenames:
        yield
        return

    # Point DATABASE_URL at the test DB and invalidate the settings cache
    # BEFORE importing admin.main, so the shared engine binds correctly.
    os.environ["DATABASE_URL"] = TEST_DATABASE_URL
    from thinktank.config import get_settings

    get_settings.cache_clear()

    from thinktank.admin.auth import require_admin
    from thinktank.admin.main import app as admin_app

    async def _fake_require_admin() -> str:
        return "admin"

    admin_app.dependency_overrides[require_admin] = _fake_require_admin
    try:
        yield
    finally:
        admin_app.dependency_overrides.pop(require_admin, None)
