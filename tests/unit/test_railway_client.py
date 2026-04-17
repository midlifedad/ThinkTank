"""Unit tests for Railway GraphQL scaling client.

Spec reference: Section 6.5, TRANS-04.
All httpx calls and database queries are mocked.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest


class TestScaleGpuService:
    """Tests for scale_gpu_service function."""

    @pytest.mark.asyncio
    @patch("thinktank.scaling.railway.httpx.AsyncClient")
    async def test_scale_up_calls_mutation(self, mock_client_cls, monkeypatch):
        """Scale up sends GraphQL mutation with numReplicas=1."""
        from thinktank.scaling.railway import scale_gpu_service

        monkeypatch.setenv("RAILWAY_API_KEY", "test-key")
        monkeypatch.setenv("RAILWAY_GPU_SERVICE_ID", "svc-123")
        monkeypatch.setenv("RAILWAY_ENVIRONMENT_ID", "env-456")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": {"serviceInstanceUpdate": True}}
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        result = await scale_gpu_service(1)

        assert result is True
        # Verify GraphQL mutation was sent
        call_kwargs = mock_client.post.call_args
        body = call_kwargs.kwargs.get("json", {})
        assert "mutation" in body.get("query", "").lower() or "mutation" in body.get("query", "")
        assert body["variables"]["input"]["numReplicas"] == 1
        assert body["variables"]["serviceId"] == "svc-123"

    @pytest.mark.asyncio
    @patch("thinktank.scaling.railway.httpx.AsyncClient")
    async def test_scale_down_calls_mutation(self, mock_client_cls, monkeypatch):
        """Scale down sends GraphQL mutation with numReplicas=0."""
        from thinktank.scaling.railway import scale_gpu_service

        monkeypatch.setenv("RAILWAY_API_KEY", "test-key")
        monkeypatch.setenv("RAILWAY_GPU_SERVICE_ID", "svc-123")
        monkeypatch.setenv("RAILWAY_ENVIRONMENT_ID", "env-456")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": {"serviceInstanceUpdate": True}}
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        result = await scale_gpu_service(0)

        assert result is True
        call_kwargs = mock_client.post.call_args
        body = call_kwargs.kwargs.get("json", {})
        assert body["variables"]["input"]["numReplicas"] == 0

    @pytest.mark.asyncio
    async def test_scale_missing_config(self, monkeypatch):
        """Returns False when env vars are missing."""
        from thinktank.scaling.railway import scale_gpu_service

        # Clear all Railway env vars
        monkeypatch.delenv("RAILWAY_API_KEY", raising=False)
        monkeypatch.delenv("RAILWAY_GPU_SERVICE_ID", raising=False)
        monkeypatch.delenv("RAILWAY_ENVIRONMENT_ID", raising=False)

        result = await scale_gpu_service(1)

        assert result is False

    @pytest.mark.asyncio
    @patch("thinktank.scaling.railway.httpx.AsyncClient")
    async def test_scale_api_error(self, mock_client_cls, monkeypatch):
        """Returns False on API error."""
        from thinktank.scaling.railway import scale_gpu_service

        monkeypatch.setenv("RAILWAY_API_KEY", "test-key")
        monkeypatch.setenv("RAILWAY_GPU_SERVICE_ID", "svc-123")
        monkeypatch.setenv("RAILWAY_ENVIRONMENT_ID", "env-456")

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=httpx.HTTPError("connection refused"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        result = await scale_gpu_service(1)

        assert result is False


class TestGetGpuReplicaCount:
    """Tests for get_gpu_replica_count function."""

    @pytest.mark.asyncio
    @patch("thinktank.scaling.railway.httpx.AsyncClient")
    async def test_get_replica_count(self, mock_client_cls, monkeypatch):
        """Returns correct replica count from GraphQL query."""
        from thinktank.scaling.railway import get_gpu_replica_count

        monkeypatch.setenv("RAILWAY_API_KEY", "test-key")
        monkeypatch.setenv("RAILWAY_GPU_SERVICE_ID", "svc-123")
        monkeypatch.setenv("RAILWAY_ENVIRONMENT_ID", "env-456")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": {
                "serviceInstance": {
                    "numReplicas": 2,
                }
            }
        }
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        result = await get_gpu_replica_count()

        assert result == 2


class TestRailwayExceptionNarrowing:
    """INTEGRATIONS-REVIEW M-04 (T6.13): scale_gpu_service and
    get_gpu_replica_count must catch only expected errors (httpx.HTTPError,
    json.JSONDecodeError, KeyError, TypeError for subscripting None) so
    true programming bugs surface instead of being silently swallowed.
    """

    @pytest.mark.asyncio
    @patch("thinktank.scaling.railway.httpx.AsyncClient")
    async def test_scale_handles_json_decode_error(self, mock_client_cls, monkeypatch):
        import json as json_module

        from thinktank.scaling.railway import scale_gpu_service

        monkeypatch.setenv("RAILWAY_API_KEY", "k")
        monkeypatch.setenv("RAILWAY_GPU_SERVICE_ID", "s")
        monkeypatch.setenv("RAILWAY_ENVIRONMENT_ID", "e")

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.side_effect = json_module.JSONDecodeError("boom", "", 0)

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        result = await scale_gpu_service(1)
        assert result is False

    @pytest.mark.asyncio
    @patch("thinktank.scaling.railway.httpx.AsyncClient")
    async def test_get_replica_handles_missing_service_instance(self, mock_client_cls, monkeypatch):
        """serviceInstance=null in response shouldn't raise."""
        from thinktank.scaling.railway import get_gpu_replica_count

        monkeypatch.setenv("RAILWAY_API_KEY", "k")
        monkeypatch.setenv("RAILWAY_GPU_SERVICE_ID", "s")
        monkeypatch.setenv("RAILWAY_ENVIRONMENT_ID", "e")

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"data": {"serviceInstance": None}}

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        result = await get_gpu_replica_count()
        assert result is None

    @pytest.mark.asyncio
    @patch("thinktank.scaling.railway.httpx.AsyncClient")
    async def test_get_replica_handles_missing_key(self, mock_client_cls, monkeypatch):
        """Missing 'numReplicas' key shouldn't raise KeyError."""
        from thinktank.scaling.railway import get_gpu_replica_count

        monkeypatch.setenv("RAILWAY_API_KEY", "k")
        monkeypatch.setenv("RAILWAY_GPU_SERVICE_ID", "s")
        monkeypatch.setenv("RAILWAY_ENVIRONMENT_ID", "e")

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"data": {"serviceInstance": {}}}

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        result = await get_gpu_replica_count()
        assert result is None

    @pytest.mark.asyncio
    @patch("thinktank.scaling.railway.httpx.AsyncClient")
    async def test_programming_errors_propagate(self, mock_client_cls, monkeypatch):
        """Unexpected bugs (e.g. attribute errors) must not be swallowed."""
        from thinktank.scaling.railway import scale_gpu_service

        monkeypatch.setenv("RAILWAY_API_KEY", "k")
        monkeypatch.setenv("RAILWAY_GPU_SERVICE_ID", "s")
        monkeypatch.setenv("RAILWAY_ENVIRONMENT_ID", "e")

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=AttributeError("unexpected programming bug"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        with pytest.raises(AttributeError):
            await scale_gpu_service(1)


class TestManageGpuScaling:
    """Tests for manage_gpu_scaling orchestration function."""

    @pytest.mark.asyncio
    @patch("thinktank.scaling.railway.scale_gpu_service")
    @patch("thinktank.scaling.railway.get_gpu_replica_count")
    @patch("thinktank.scaling.railway.get_config_value")
    @patch("thinktank.scaling.railway.get_queue_depth")
    async def test_manage_gpu_scaling_scale_up(self, mock_depth, mock_config, mock_replicas, mock_scale):
        """Scales up when queue depth > threshold and replicas=0."""
        from thinktank.scaling.railway import manage_gpu_scaling

        mock_session = AsyncMock()
        mock_depth.return_value = 10  # Above threshold
        mock_config.side_effect = lambda s, k, d: 5 if k == "gpu_queue_threshold" else 30
        mock_replicas.return_value = 0  # Currently scaled down
        mock_scale.return_value = True

        scaled, idle_since = await manage_gpu_scaling(mock_session, gpu_idle_since=None)

        assert scaled is True
        assert idle_since is None
        mock_scale.assert_called_once()
        assert mock_scale.call_args[0][0] == 1

    @pytest.mark.asyncio
    @patch("thinktank.scaling.railway.scale_gpu_service")
    @patch("thinktank.scaling.railway.get_gpu_replica_count")
    @patch("thinktank.scaling.railway.get_config_value")
    @patch("thinktank.scaling.railway.get_queue_depth")
    async def test_manage_gpu_scaling_scale_down(self, mock_depth, mock_config, mock_replicas, mock_scale):
        """Scales down when queue depth=0 and idle time exceeds timeout."""
        from thinktank.scaling.railway import manage_gpu_scaling

        mock_session = AsyncMock()
        mock_depth.return_value = 0
        mock_config.side_effect = lambda s, k, d: 5 if k == "gpu_queue_threshold" else 30
        mock_replicas.return_value = 1  # Currently running
        mock_scale.return_value = True

        # Idle for 35 minutes (> 30 min timeout); aware UTC per migration 007.
        idle_since = datetime.now(UTC) - timedelta(minutes=35)

        scaled, new_idle = await manage_gpu_scaling(mock_session, gpu_idle_since=idle_since)

        assert scaled is True
        assert new_idle is None
        mock_scale.assert_called_once()
        assert mock_scale.call_args[0][0] == 0

    @pytest.mark.asyncio
    @patch("thinktank.scaling.railway.scale_gpu_service")
    @patch("thinktank.scaling.railway.get_gpu_replica_count")
    @patch("thinktank.scaling.railway.get_config_value")
    @patch("thinktank.scaling.railway.get_queue_depth")
    async def test_manage_gpu_scaling_no_action(self, mock_depth, mock_config, mock_replicas, mock_scale):
        """No scaling action when queue depth=0 but idle time < timeout."""
        from thinktank.scaling.railway import manage_gpu_scaling

        mock_session = AsyncMock()
        mock_depth.return_value = 0
        mock_config.side_effect = lambda s, k, d: 5 if k == "gpu_queue_threshold" else 30
        mock_replicas.return_value = 1

        # Idle for only 10 minutes (< 30 min timeout); aware UTC per migration 007.
        idle_since = datetime.now(UTC) - timedelta(minutes=10)

        scaled, new_idle = await manage_gpu_scaling(mock_session, gpu_idle_since=idle_since)

        assert scaled is False
        assert new_idle == idle_since  # Keep tracking idle time
        mock_scale.assert_not_called()

    @pytest.mark.asyncio
    @patch("thinktank.scaling.railway.scale_gpu_service")
    @patch("thinktank.scaling.railway.get_gpu_replica_count")
    @patch("thinktank.scaling.railway.get_config_value")
    @patch("thinktank.scaling.railway.get_queue_depth")
    async def test_manage_gpu_scaling_start_idle_timer(self, mock_depth, mock_config, mock_replicas, mock_scale):
        """Starts idle timer when queue depth first reaches 0."""
        from thinktank.scaling.railway import manage_gpu_scaling

        mock_session = AsyncMock()
        mock_depth.return_value = 0
        mock_config.side_effect = lambda s, k, d: 5 if k == "gpu_queue_threshold" else 30
        mock_replicas.return_value = 1

        scaled, new_idle = await manage_gpu_scaling(mock_session, gpu_idle_since=None)

        assert scaled is False
        assert new_idle is not None  # Timer started
        mock_scale.assert_not_called()
