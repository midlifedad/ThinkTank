"""Unit tests for GPU scaling scheduler in worker loop.

Tests the _gpu_scaling_scheduler function that runs manage_gpu_scaling
on an interval, respects shutdown events, and propagates idle_since state.
"""

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_session_factory():
    """Create a mock session factory that works with `async with session_factory() as s:`."""
    mock_session = AsyncMock()

    @asynccontextmanager
    async def _session_ctx():
        yield mock_session

    factory = MagicMock(side_effect=lambda: _session_ctx())
    return factory, mock_session


@patch("thinktank.worker.loop.manage_gpu_scaling", new_callable=AsyncMock)
async def test_scheduler_calls_manage_gpu_scaling(mock_manage):
    """Mock session_factory + manage_gpu_scaling, run one iteration -> manage_gpu_scaling called."""
    from thinktank.worker.loop import _gpu_scaling_scheduler

    session_factory, _ = _make_session_factory()
    shutdown_event = asyncio.Event()

    async def stop_after_one_call(*args, **kwargs):
        shutdown_event.set()
        return (False, None)

    mock_manage.side_effect = stop_after_one_call

    # Run with a very short interval so sleep completes quickly
    await _gpu_scaling_scheduler(session_factory, 0.01, shutdown_event)

    mock_manage.assert_awaited_once()


@patch("thinktank.worker.loop.manage_gpu_scaling", new_callable=AsyncMock)
async def test_scheduler_respects_shutdown(mock_manage):
    """Set shutdown_event immediately -> scheduler exits without calling manage_gpu_scaling."""
    from thinktank.worker.loop import _gpu_scaling_scheduler

    session_factory, _ = _make_session_factory()
    shutdown_event = asyncio.Event()
    shutdown_event.set()  # Already set before scheduler starts

    await _gpu_scaling_scheduler(session_factory, 0.01, shutdown_event)

    mock_manage.assert_not_awaited()


@patch("thinktank.worker.loop.manage_gpu_scaling", new_callable=AsyncMock)
async def test_scheduler_passes_idle_since(mock_manage):
    """After first call returns idle_since, second call passes it through."""
    from thinktank.worker.loop import _gpu_scaling_scheduler

    idle_time = datetime.now(UTC)
    call_count = 0

    session_factory, _ = _make_session_factory()
    shutdown_event = asyncio.Event()

    async def track_calls(session, gpu_idle_since):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # First call: return an idle_since timestamp
            return (False, idle_time)
        else:
            # Second call: should receive the idle_since from first call
            assert gpu_idle_since == idle_time
            shutdown_event.set()
            return (False, idle_time)

    mock_manage.side_effect = track_calls

    await _gpu_scaling_scheduler(session_factory, 0.01, shutdown_event)

    assert call_count == 2
