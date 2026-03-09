"""Contract tests for the JobHandler protocol and registry.

Verifies:
- Dummy handler conforming to JobHandler protocol can be registered and dispatched
- register_handler raises ValueError on duplicate registration
- get_handler returns None for unregistered job type
- Registered handler receives correct session and job arguments
"""

import uuid
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.thinktank.handlers.base import JobHandler
from src.thinktank.handlers.registry import JOB_HANDLERS, get_handler, register_handler
from src.thinktank.models.job import Job
from tests.factories import make_job


@pytest.fixture(autouse=True)
def _clean_registry():
    """Save and restore JOB_HANDLERS state around each test."""
    saved = dict(JOB_HANDLERS)
    JOB_HANDLERS.clear()
    yield
    JOB_HANDLERS.clear()
    JOB_HANDLERS.update(saved)


class TestJobHandlerProtocol:
    """Verify that conforming callables satisfy the JobHandler protocol."""

    def test_async_function_satisfies_protocol(self):
        """A plain async function with (session, job) -> None signature conforms."""

        async def handler(session: AsyncSession, job: Job) -> None:
            pass

        # Structural subtyping: if it has the right signature, it should
        # be accepted by register_handler without error
        register_handler("test_type", handler)
        assert get_handler("test_type") is handler

    def test_async_callable_class_satisfies_protocol(self):
        """A class with async __call__(session, job) -> None conforms."""

        class MyHandler:
            async def __call__(self, session: AsyncSession, job: Job) -> None:
                pass

        handler = MyHandler()
        register_handler("test_class_type", handler)
        assert get_handler("test_class_type") is handler


class TestHandlerRegistry:
    """Verify register_handler and get_handler behavior."""

    def test_register_and_get_handler(self):
        """register_handler adds to JOB_HANDLERS, get_handler retrieves."""

        async def handler(session: AsyncSession, job: Job) -> None:
            pass

        register_handler("my_job", handler)
        assert get_handler("my_job") is handler
        assert "my_job" in JOB_HANDLERS

    def test_register_duplicate_raises_value_error(self):
        """register_handler raises ValueError on duplicate registration."""

        async def handler1(session: AsyncSession, job: Job) -> None:
            pass

        async def handler2(session: AsyncSession, job: Job) -> None:
            pass

        register_handler("dup_type", handler1)
        with pytest.raises(ValueError, match="Handler already registered"):
            register_handler("dup_type", handler2)

    def test_get_handler_returns_none_for_unregistered(self):
        """get_handler returns None for an unregistered job type."""
        assert get_handler("nonexistent_type") is None


class TestHandlerDispatch:
    """Verify that a registered handler receives correct arguments."""

    @pytest.mark.asyncio
    async def test_handler_receives_correct_session_and_job(self):
        """Handler receives the exact session and job objects passed to it."""
        received_args: list = []

        async def tracking_handler(session: AsyncSession, job: Job) -> None:
            received_args.append((session, job))

        register_handler("track_type", tracking_handler)

        mock_session = AsyncMock(spec=AsyncSession)
        test_job = make_job(job_type="track_type")

        handler = get_handler("track_type")
        assert handler is not None
        await handler(mock_session, test_job)

        assert len(received_args) == 1
        assert received_args[0][0] is mock_session
        assert received_args[0][1] is test_job

    @pytest.mark.asyncio
    async def test_handler_exception_propagates(self):
        """Handlers that raise should propagate (worker loop catches)."""

        async def failing_handler(session: AsyncSession, job: Job) -> None:
            raise RuntimeError("handler failed")

        register_handler("fail_type", failing_handler)

        mock_session = AsyncMock(spec=AsyncSession)
        test_job = make_job(job_type="fail_type")

        handler = get_handler("fail_type")
        assert handler is not None
        with pytest.raises(RuntimeError, match="handler failed"):
            await handler(mock_session, test_job)
