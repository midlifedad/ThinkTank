"""Integration test fixtures.

Applies _cleanup_tables as autouse for all integration tests
so each test gets clean database tables.
"""

import pytest


@pytest.fixture(autouse=True)
async def _auto_cleanup(_cleanup_tables):
    """Auto-apply table cleanup for all integration tests."""
    yield
