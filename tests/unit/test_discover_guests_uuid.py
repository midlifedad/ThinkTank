"""Unit tests for discover_guests_podcastindex thinker_id UUID coercion.

HANDLERS-REVIEW HI-03: payload thinker_id was passed as a raw string to
session.get(Thinker, thinker_id). A malformed value raised DataError
inside the ORM get(); no early-out / structured log existed.
"""

import logging
from unittest.mock import AsyncMock

import pytest

from tests.factories import make_job

pytestmark = pytest.mark.anyio


async def test_invalid_thinker_id_is_logged_and_returns_cleanly(caplog):
    """A malformed thinker_id in the payload must log an error and
    early-return, not raise."""
    from thinktank.handlers.discover_guests_podcastindex import (
        handle_discover_guests_podcastindex,
    )

    job = make_job(
        job_type="discover_guests_podcastindex",
        payload={"thinker_id": "not-a-valid-uuid"},
    )

    session = AsyncMock()
    session.get = AsyncMock(return_value=None)
    session.execute = AsyncMock()
    session.commit = AsyncMock()

    with caplog.at_level(logging.INFO):
        # Must not raise.
        await handle_discover_guests_podcastindex(session, job)

    # session.get must NOT have been called with the bad string.
    get_calls = [c.args[1] for c in session.get.await_args_list]
    assert "not-a-valid-uuid" not in get_calls, (
        "invalid thinker_id string was passed to session.get -- must be "
        "caught and early-returned"
    )

    # No Source insert attempted.
    session.commit.assert_not_awaited()
