"""Unit tests for scan_for_candidates UUID coercion.

HANDLERS-REVIEW HI-02: handler passed raw strings from
`job.payload["content_ids"]` directly to `session.get(Content, content_id_str)`.
Invalid UUIDs raised an uninformative DataError / silently missed the row
instead of being logged and skipped.
"""

import logging
import uuid
from unittest.mock import AsyncMock

import pytest

from tests.factories import make_job

pytestmark = pytest.mark.anyio


async def test_invalid_uuid_is_logged_and_skipped(caplog):
    """An invalid UUID string in content_ids must be logged and skipped,
    not raised. Valid UUIDs in the same batch must still be processed.
    """
    from thinktank.handlers.scan_for_candidates import handle_scan_for_candidates

    valid_id = uuid.uuid4()
    job = make_job(
        job_type="scan_for_candidates",
        payload={"content_ids": ["not-a-uuid", str(valid_id)]},
    )

    session = AsyncMock()
    session.commit = AsyncMock()
    session.add = AsyncMock()
    session.get = AsyncMock(return_value=None)  # all content lookups miss

    async def exec_no_similar(_stmt):
        mock = AsyncMock()
        mock.scalars.return_value.all.return_value = []
        mock.scalar_one_or_none.return_value = None
        mock.scalar.return_value = 0
        return mock

    session.execute = AsyncMock(side_effect=exec_no_similar)

    # Patch out quota / trigram helpers to simple stubs so we exercise only
    # the UUID coercion path.
    from thinktank.handlers import scan_for_candidates as mod

    original_check = mod.check_daily_quota
    original_pending = mod.get_pending_candidate_count
    original_sim_thinkers = mod.find_similar_thinkers
    original_sim_cands = mod.find_similar_candidates
    original_trigger = mod.should_trigger_llm_review

    mod.check_daily_quota = AsyncMock(return_value=(True, 0, 100))
    mod.get_pending_candidate_count = AsyncMock(return_value=0)
    mod.find_similar_thinkers = AsyncMock(return_value=[])
    mod.find_similar_candidates = AsyncMock(return_value=[])
    mod.should_trigger_llm_review = lambda a, b: False

    try:
        with caplog.at_level(logging.WARNING):
            await handle_scan_for_candidates(session, job)
    finally:
        mod.check_daily_quota = original_check
        mod.get_pending_candidate_count = original_pending
        mod.find_similar_thinkers = original_sim_thinkers
        mod.find_similar_candidates = original_sim_cands
        mod.should_trigger_llm_review = original_trigger

    # session.get was called for the VALID uuid (Content lookup) but NOT
    # for the invalid string.
    get_calls = session.get.await_args_list
    call_args = [c.args[1] for c in get_calls]
    assert valid_id in call_args, "valid UUID must still be queried"
    assert "not-a-uuid" not in call_args, "invalid UUID string was passed to session.get -- must be skipped"

    # Handler must not have crashed. session.commit was reached.
    session.commit.assert_awaited()
