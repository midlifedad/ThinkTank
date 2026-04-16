"""Contract test fixtures.

Applies _cleanup_tables as autouse for contract tests that
use the real database, ensuring each test gets clean tables.

Also bypasses the admin ``require_admin`` dependency for contract tests
that use an admin client. This is applied only when the test explicitly
requests ``admin_client`` as a fixture (detected via request.fixturenames)
so that pure API contract tests are not forced to import the admin app
during collection — that would cause the cached ``thinktank.database``
engine to bind to the non-test DB URL and make tables un-creatable.
"""

import os

import pytest

TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://thinktank_test:thinktank_test@localhost:5433/thinktank_test",
)


@pytest.fixture(autouse=True)
async def _auto_cleanup(_cleanup_tables):
    """Auto-apply table cleanup for all contract tests."""
    yield


@pytest.fixture(autouse=True)
def _auto_admin_auth_override(request):
    """Bypass require_admin — but only for tests that use admin_client.

    This avoids importing the admin app for pure API contract tests,
    which would otherwise force the shared ``thinktank.database.engine``
    to be created before the ``client`` fixture has a chance to point
    DATABASE_URL at the test database.

    IMPORTANT: when this autouse fires it MUST ensure DATABASE_URL is
    pointed at the test database BEFORE importing ``thinktank.admin.main``.
    Otherwise the module-level ``thinktank.database.engine`` is bound to
    whatever URL settings happen to hold at import time (potentially the
    production default) and every subsequent test that uses the shared
    session factory hits the wrong database.
    """
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
