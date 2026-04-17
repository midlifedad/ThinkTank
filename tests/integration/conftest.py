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
def _auto_admin_auth_override(request, session_factory):
    """Bypass require_admin and override get_session for admin_client tests.

    Two overrides:

    1. ``require_admin`` → returns "admin" so pre-existing admin integration
       tests (written before auth existed) still run. The dedicated auth
       suite (``test_admin_auth.py``) opts out to exercise the real dep.

    2. ``get_session`` → yields from the test ``session_factory`` so the
       admin app shares the test engine's connection pool. Without this,
       the app's module-level engine and the test engine race on the same
       database, producing asyncpg ``InterfaceError`` and TRUNCATE
       deadlocks at fixture teardown.

    Both overrides only fire when the test actually uses ``admin_client``
    (detected via ``request.fixturenames``) to avoid importing the admin
    app for tests that don't need it.
    """
    # The dedicated CSRF suite exercises the real middleware and must not
    # have it bypassed.
    skip_csrf_bypass = "test_admin_csrf" in request.node.nodeid
    # The dedicated auth suite needs the real require_admin dep (otherwise
    # it can't test auth). But auth tests POST without CSRF headers and
    # predate HI-05, so they still need the CSRF bypass.
    skip_auth_override = "test_admin_auth" in request.node.nodeid

    if "admin_client" not in request.fixturenames:
        yield
        return

    if skip_csrf_bypass and skip_auth_override:
        # Neither file actually sets admin_client == admin_client, but
        # belt-and-braces: if the node.id somehow matches both, fall back
        # to no overrides.
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

    # Pre-existing admin integration tests (written before HI-05 added
    # CSRF middleware) don't send X-CSRF-Token headers. Find the
    # CSRFMiddleware instance in the built stack and swap its dispatch_func
    # for a passthrough; the dedicated ``test_admin_csrf`` suite opts out
    # via ``skip_csrf_bypass`` and exercises the real enforcement.
    async def _passthrough(request_, call_next):  # type: ignore[no-untyped-def]
        return await call_next(request_)

    _csrf_instance = None
    _original_dispatch_func = None
    if not skip_csrf_bypass:
        _stack = admin_app.middleware_stack or admin_app.build_middleware_stack()
        admin_app.middleware_stack = _stack
        _node = _stack
        while _node is not None:
            if isinstance(_node, CSRFMiddleware):
                _csrf_instance = _node
                break
            _node = getattr(_node, "app", None)
        if _csrf_instance is not None:
            _original_dispatch_func = _csrf_instance.dispatch_func
            _csrf_instance.dispatch_func = _passthrough

    if not skip_auth_override:
        admin_app.dependency_overrides[require_admin] = _fake_require_admin
    admin_app.dependency_overrides[get_session] = _override_get_session
    try:
        yield
    finally:
        admin_app.dependency_overrides.pop(require_admin, None)
        admin_app.dependency_overrides.pop(get_session, None)
        if _csrf_instance is not None and _original_dispatch_func is not None:
            _csrf_instance.dispatch_func = _original_dispatch_func
