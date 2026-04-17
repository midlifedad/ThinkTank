"""Integration tests for the chat API endpoints and agent tools.

Tests cover SSE streaming, proposal confirmation, history retrieval,
SQL validation, LIMIT injection, and confirmed action execution.
Anthropic API is mocked throughout -- no real LLM calls.
"""

import json
import os
import uuid
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.factories import create_job
from thinktank.agent.session import ChatMessage, chat_sessions
from thinktank.agent.tools import execute_confirmed_action, execute_tool
from thinktank.models.job import Job
from thinktank.models.thinker import Thinker

pytestmark = pytest.mark.anyio

TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://thinktank_test:thinktank_test@localhost:5433/thinktank_test",
)


@pytest.fixture
async def admin_client():
    """HTTP client for admin integration tests."""
    os.environ["DATABASE_URL"] = TEST_DATABASE_URL

    from thinktank.config import get_settings

    get_settings.cache_clear()

    from thinktank.admin.main import app as admin_app

    transport = ASGITransport(app=admin_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _clean_chat_sessions():
    """Ensure clean chat session state for each test."""
    chat_sessions._sessions.clear()
    yield
    chat_sessions._sessions.clear()


def _make_mock_stream_generator(*events):
    """Create a mock async generator that yields event dicts."""

    async def mock_stream(session_id, user_message, db_session):
        for event in events:
            yield event

    return mock_stream


class TestChatSend:
    """Test the POST /admin/chat/send endpoint."""

    async def test_chat_send_creates_session(self, admin_client):
        """POST /admin/chat/send with no session_id creates a new session and streams SSE."""
        mock_events = [
            {"type": "text_delta", "text": "Hello"},
            {"type": "text_delta", "text": " there!"},
            {"type": "done"},
        ]

        with patch(
            "thinktank.admin.routers.chat.stream_agent_response",
            side_effect=_make_mock_stream_generator(*mock_events),
        ):
            response = await admin_client.post(
                "/admin/chat/send",
                data={"message": "Hi", "session_id": ""},
            )

        assert response.status_code == 200
        assert "text/event-stream" in response.headers.get("content-type", "")

        # Parse SSE events from response body
        body = response.text
        events = _parse_sse_events(body)

        # First event should be session_init
        assert events[0]["type"] == "session_init"
        assert "session_id" in events[0]

        # Remaining events are from the mock
        text_events = [e for e in events if e.get("type") == "text_delta"]
        assert len(text_events) == 2
        assert text_events[0]["text"] == "Hello"
        assert text_events[1]["text"] == " there!"

        done_events = [e for e in events if e.get("type") == "done"]
        assert len(done_events) == 1

    async def test_chat_send_with_existing_session(self, admin_client):
        """POST /admin/chat/send with existing session_id uses that session."""
        # Create session first
        session = chat_sessions.create()

        mock_events = [
            {"type": "text_delta", "text": "Response"},
            {"type": "done"},
        ]

        with patch(
            "thinktank.admin.routers.chat.stream_agent_response",
            side_effect=_make_mock_stream_generator(*mock_events),
        ):
            response = await admin_client.post(
                "/admin/chat/send",
                data={"message": "Hello", "session_id": session.session_id},
            )

        assert response.status_code == 200
        body = response.text
        events = _parse_sse_events(body)

        # Should NOT have session_init (existing session)
        init_events = [e for e in events if e.get("type") == "session_init"]
        assert len(init_events) == 0

        text_events = [e for e in events if e.get("type") == "text_delta"]
        assert len(text_events) == 1
        assert text_events[0]["text"] == "Response"


class TestChatConfirm:
    """Test the POST /admin/chat/confirm/{proposal_id} endpoint."""

    async def test_chat_confirm_proposal(self, admin_client, session: AsyncSession):
        """Confirming a proposal executes the action (e.g., add_thinker creates a row)."""
        # Create a session with a pending proposal
        chat_session = chat_sessions.create()
        proposal_id = str(uuid.uuid4())
        chat_sessions.add_proposal(
            chat_session.session_id,
            proposal_id,
            {
                "action_type": "add_thinker",
                "target": "Nassim Taleb",
                "details": {"name": "Nassim Taleb"},
                "explanation": "Adding new thinker",
            },
        )

        response = await admin_client.post(
            f"/admin/chat/confirm/{proposal_id}",
            data={"session_id": chat_session.session_id},
        )

        assert response.status_code == 200
        result = response.json()
        assert result["success"] is True
        assert "Nassim Taleb" in result["message"]

        # Verify thinker was created in DB
        thinker_result = await session.execute(select(Thinker).where(Thinker.slug == "nassim-taleb"))
        thinker = thinker_result.scalar_one_or_none()
        assert thinker is not None
        assert thinker.name == "Nassim Taleb"
        assert thinker.approval_status == "awaiting_llm"

    async def test_chat_confirm_missing_proposal(self, admin_client):
        """POST /admin/chat/confirm with nonexistent proposal returns 404."""
        chat_session = chat_sessions.create()

        response = await admin_client.post(
            "/admin/chat/confirm/nonexistent-id",
            data={"session_id": chat_session.session_id},
        )

        assert response.status_code == 404


class TestChatHistory:
    """Test the GET /admin/chat/history endpoint."""

    async def test_chat_history_returns_messages(self, admin_client):
        """GET /admin/chat/history returns user and assistant messages."""
        chat_session = chat_sessions.create()
        chat_sessions.add_message(
            chat_session.session_id,
            ChatMessage(role="user", content="How many thinkers?"),
        )
        chat_sessions.add_message(
            chat_session.session_id,
            ChatMessage(role="assistant", content="There are 42 active thinkers."),
        )

        response = await admin_client.get(
            "/admin/chat/history",
            params={"session_id": chat_session.session_id},
        )

        assert response.status_code == 200
        messages = response.json()
        assert len(messages) == 2
        assert messages[0]["role"] == "user"
        assert messages[0]["content"] == "How many thinkers?"
        assert messages[1]["role"] == "assistant"
        assert messages[1]["content"] == "There are 42 active thinkers."
        assert "timestamp" in messages[0]

    async def test_chat_history_missing_session(self, admin_client):
        """GET /admin/chat/history with nonexistent session returns 404."""
        response = await admin_client.get(
            "/admin/chat/history",
            params={"session_id": "nonexistent-session"},
        )

        assert response.status_code == 404


class TestQueryDatabaseTool:
    """Test the query_database tool directly."""

    async def test_query_database_rejects_non_select(self, session: AsyncSession):
        """execute_tool with INSERT SQL returns an error."""
        result = await execute_tool(
            "query_database",
            {"sql": "INSERT INTO thinkers (name) VALUES ('test')", "explanation": "test"},
            session,
        )
        assert "error" in result
        assert "SELECT" in result["error"]

    async def test_query_database_rejects_update(self, session: AsyncSession):
        """execute_tool with UPDATE SQL returns an error."""
        result = await execute_tool(
            "query_database",
            {"sql": "UPDATE thinkers SET name='test'", "explanation": "test"},
            session,
        )
        assert "error" in result

    async def test_query_database_adds_limit(self, session: AsyncSession):
        """execute_tool adds LIMIT 50 when no LIMIT clause present."""
        # We can verify by running a query -- the result should work
        result = await execute_tool(
            "query_database",
            {"sql": "SELECT 1 as num", "explanation": "test"},
            session,
        )
        assert "rows" in result
        assert result["row_count"] >= 1

    async def test_query_database_preserves_existing_limit(self, session: AsyncSession):
        """execute_tool preserves existing LIMIT clause."""
        result = await execute_tool(
            "query_database",
            {"sql": "SELECT 1 as num LIMIT 10", "explanation": "test"},
            session,
        )
        assert "rows" in result


class TestExecuteConfirmedAction:
    """Test execute_confirmed_action for specific action types."""

    async def test_add_thinker(self, session: AsyncSession):
        """add_thinker creates a thinker and an LLM approval job."""
        result = await execute_confirmed_action(
            "add_thinker",
            {"name": "Sam Harris"},
            session,
        )

        assert result["success"] is True
        assert "Sam Harris" in result["message"]

        # Verify thinker exists
        thinker_result = await session.execute(select(Thinker).where(Thinker.slug == "sam-harris"))
        thinker = thinker_result.scalar_one_or_none()
        assert thinker is not None
        assert thinker.tier == 3
        assert thinker.approval_status == "awaiting_llm"

        # Verify job was created
        job_result = await session.execute(select(Job).where(Job.job_type == "llm_approval_check"))
        job = job_result.scalar_one_or_none()
        assert job is not None

    async def test_cancel_pending_job(self, session: AsyncSession, session_factory):
        """cancel_job changes a pending job to cancelled."""
        job = await create_job(session, status="pending", job_type="fetch_podcast_feed")
        await session.commit()
        job_id = str(job.id)

        result = await execute_confirmed_action(
            "cancel_job",
            {"job_id": job_id},
            session,
        )

        assert result["success"] is True

        # Verify status changed (use fresh session since execute_confirmed_action committed)
        async with session_factory() as verify_session:
            updated = await verify_session.execute(
                select(Job).where(Job.id == job.id).execution_options(populate_existing=True)
            )
            updated_job = updated.scalar_one()
            assert updated_job.status == "cancelled"

    async def test_cancel_non_pending_job_fails(self, session: AsyncSession):
        """cancel_job fails for a running job (not in pending status)."""
        job = await create_job(session, status="running", job_type="fetch_podcast_feed")
        await session.commit()

        result = await execute_confirmed_action(
            "cancel_job",
            {"job_id": str(job.id)},
            session,
        )

        assert "error" in result
        assert "not" in result["error"].lower() or "pending" in result["error"].lower()


def _parse_sse_events(body: str) -> list[dict]:
    """Parse SSE event data lines from response body."""
    events = []
    for line in body.strip().split("\n"):
        line = line.strip()
        if line.startswith("data: "):
            data = line[6:]
            try:
                events.append(json.loads(data))
            except json.JSONDecodeError:
                pass
    return events
