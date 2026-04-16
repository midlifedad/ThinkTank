"""Data-layer test fixtures.

Applies _cleanup_tables as autouse so each data-layer test runs against
a clean schema, mirroring the integration test setup.
"""

import pytest


@pytest.fixture(autouse=True)
async def _auto_cleanup(_cleanup_tables):
    """Auto-apply table cleanup for all data-layer tests."""
    yield
