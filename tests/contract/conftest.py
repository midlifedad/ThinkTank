"""Contract test fixtures.

Applies _cleanup_tables as autouse for contract tests that
use the real database, ensuring each test gets clean tables.
"""

import pytest


@pytest.fixture(autouse=True)
async def _auto_cleanup(_cleanup_tables):
    """Auto-apply table cleanup for all contract tests."""
    yield
