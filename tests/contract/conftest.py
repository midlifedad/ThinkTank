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
def _auto_admin_auth_override(request, session_factory):
    """Bypass require_admin and override get_session for admin_client tests.

    Mirrors the integration conftest override. See that file for full
    rationale. Summary: without the ``get_session`` override, the admin
    app's module-level engine and the test engine race on the same DB,
    causing asyncpg ``InterfaceError`` and TRUNCATE deadlocks.
    """
    if "admin_client" not in request.fixturenames:
        yield
        return

    os.environ["DATABASE_URL"] = TEST_DATABASE_URL
    from thinktank.config import get_settings

    get_settings.cache_clear()

    from thinktank.admin.auth import require_admin
    from thinktank.admin.csrf import CSRFMiddleware
    from thinktank.admin.dependencies import get_session
    from thinktank.admin.main import app as admin_app

    async def _fake_require_admin() -> str:
        return "admin"

    async def _override_get_session():
        async with session_factory() as s:
            yield s

    # Contract tests predate HI-05 CSRF middleware and POST without
    # X-CSRF-Token headers. Find the CSRFMiddleware instance in the
    # built stack and swap its dispatch_func for a passthrough. See
    # tests/integration/conftest.py for the full rationale.
    async def _passthrough(request_, call_next):  # type: ignore[no-untyped-def]
        return await call_next(request_)

    _stack = admin_app.middleware_stack or admin_app.build_middleware_stack()
    admin_app.middleware_stack = _stack
    _csrf_instance = None
    _original_dispatch_func = None
    _node = _stack
    while _node is not None:
        if isinstance(_node, CSRFMiddleware):
            _csrf_instance = _node
            break
        _node = getattr(_node, "app", None)
    if _csrf_instance is not None:
        _original_dispatch_func = _csrf_instance.dispatch_func
        _csrf_instance.dispatch_func = _passthrough

    admin_app.dependency_overrides[require_admin] = _fake_require_admin
    admin_app.dependency_overrides[get_session] = _override_get_session
    try:
        yield
    finally:
        admin_app.dependency_overrides.pop(require_admin, None)
        admin_app.dependency_overrides.pop(get_session, None)
        if _csrf_instance is not None and _original_dispatch_func is not None:
            _csrf_instance.dispatch_func = _original_dispatch_func
