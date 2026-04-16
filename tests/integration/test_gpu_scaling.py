"""Integration tests for manage_gpu_scaling with real DB and mocked Railway API.

Tests verify that manage_gpu_scaling correctly reads queue depth from the
real database and makes appropriate scaling decisions, with Railway API
calls (scale_gpu_service, get_gpu_replica_count) mocked to avoid
external service dependencies.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from thinktank.scaling.railway import manage_gpu_scaling
from tests.factories import create_job


@patch("thinktank.scaling.railway.scale_gpu_service", new_callable=AsyncMock)
@patch("thinktank.scaling.railway.get_gpu_replica_count", new_callable=AsyncMock)
async def test_scale_up_when_queue_exceeds_threshold(
    mock_get_replicas, mock_scale, session
):
    """Create process_content jobs exceeding threshold -> scale_gpu_service called with 1."""
    # Create jobs exceeding default threshold (gpu_queue_threshold=5)
    for i in range(6):
        await create_job(session, job_type="process_content", status="pending")
    await session.commit()

    mock_get_replicas.return_value = 0
    mock_scale.return_value = True

    scaled, idle_since = await manage_gpu_scaling(session, gpu_idle_since=None)

    assert scaled is True
    mock_scale.assert_called_once()
    assert mock_scale.call_args[0][0] == 1  # replicas=1


@patch("thinktank.scaling.railway.scale_gpu_service", new_callable=AsyncMock)
@patch("thinktank.scaling.railway.get_gpu_replica_count", new_callable=AsyncMock)
async def test_no_scale_when_queue_below_threshold(
    mock_get_replicas, mock_scale, session
):
    """Fewer jobs than threshold -> scale_gpu_service NOT called."""
    # Create only 3 jobs (below default threshold of 5)
    for i in range(3):
        await create_job(session, job_type="process_content", status="pending")
    await session.commit()

    mock_get_replicas.return_value = 1

    scaled, idle_since = await manage_gpu_scaling(session, gpu_idle_since=None)

    assert scaled is False
    mock_scale.assert_not_called()


@patch("thinktank.scaling.railway.scale_gpu_service", new_callable=AsyncMock)
@patch("thinktank.scaling.railway.get_gpu_replica_count", new_callable=AsyncMock)
async def test_scale_down_after_idle_timeout(
    mock_get_replicas, mock_scale, session
):
    """Queue depth=0, idle_since older than timeout -> scale_gpu_service(0) called."""
    # No jobs in queue -- depth is 0
    # gpu_idle_since is older than timeout (default 30 min)
    old_idle = datetime.now(UTC) - timedelta(minutes=35)

    mock_get_replicas.return_value = 1
    mock_scale.return_value = True

    scaled, idle_since = await manage_gpu_scaling(session, gpu_idle_since=old_idle)

    assert scaled is True
    mock_scale.assert_called_once()
    assert mock_scale.call_args[0][0] == 0  # replicas=0


@patch("thinktank.scaling.railway.scale_gpu_service", new_callable=AsyncMock)
@patch("thinktank.scaling.railway.get_gpu_replica_count", new_callable=AsyncMock)
async def test_no_scale_down_when_idle_under_timeout(
    mock_get_replicas, mock_scale, session
):
    """Queue depth=0, idle_since within timeout -> scale NOT called, idle_since preserved."""
    # gpu_idle_since is within the timeout window (only 5 min ago)
    recent_idle = datetime.now(UTC) - timedelta(minutes=5)

    mock_get_replicas.return_value = 1

    scaled, idle_since = await manage_gpu_scaling(session, gpu_idle_since=recent_idle)

    assert scaled is False
    mock_scale.assert_not_called()
    assert idle_since == recent_idle


@patch("thinktank.scaling.railway.scale_gpu_service", new_callable=AsyncMock)
@patch("thinktank.scaling.railway.get_gpu_replica_count", new_callable=AsyncMock)
async def test_idle_timer_starts_when_queue_empty(
    mock_get_replicas, mock_scale, session
):
    """Queue depth=0, gpu_idle_since=None -> returns (False, datetime) to start tracking."""
    mock_get_replicas.return_value = 1

    scaled, idle_since = await manage_gpu_scaling(session, gpu_idle_since=None)

    assert scaled is False
    assert idle_since is not None
    assert isinstance(idle_since, datetime)


@patch("thinktank.scaling.railway.scale_gpu_service", new_callable=AsyncMock)
@patch("thinktank.scaling.railway.get_gpu_replica_count", new_callable=AsyncMock)
async def test_idle_timer_resets_when_queue_has_jobs(
    mock_get_replicas, mock_scale, session
):
    """Queue depth > 0, gpu_idle_since set -> returns (False, None) resetting idle timer."""
    # Create some jobs so depth > 0
    for i in range(3):
        await create_job(session, job_type="process_content", status="pending")
    await session.commit()

    old_idle = datetime.now(UTC) - timedelta(minutes=10)
    mock_get_replicas.return_value = 1

    scaled, idle_since = await manage_gpu_scaling(session, gpu_idle_since=old_idle)

    assert scaled is False
    assert idle_since is None  # Reset because queue has jobs
