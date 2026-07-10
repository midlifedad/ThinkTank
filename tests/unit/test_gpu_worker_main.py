"""Unit tests for the GPU worker FastAPI service startup.

The gpu_worker package had ZERO test coverage until Phase B: its lifespan
crashed with a TypeError on the service's first-ever production launch
(structlog's ``event`` is the positional first argument -- passing
``event=`` as a kwarg collides). The bug shipped in Phase 4 and was never
exposed because nothing launched the app until A3 fixed the Dockerfile CMD.

These tests run the lifespan with the model load mocked, so any logging or
startup-path regression fails in CI instead of in a Railway crash loop.
"""

from unittest.mock import patch

import pytest

from thinktank.gpu_worker.main import app, lifespan

pytestmark = pytest.mark.anyio


class TestLifespanStartup:
    async def test_lifespan_completes_without_error(self):
        """Startup logging must not raise (structlog event-kwarg collision)."""
        with patch("thinktank.gpu_worker.main.load_model") as mock_load:
            async with lifespan(app):
                pass
        mock_load.assert_called_once()

    async def test_model_loaded_before_serving(self):
        """load_model runs inside startup, before the app yields to serving."""
        calls: list[str] = []
        with patch("thinktank.gpu_worker.main.load_model", side_effect=lambda: calls.append("loaded")):
            async with lifespan(app):
                assert calls == ["loaded"]
