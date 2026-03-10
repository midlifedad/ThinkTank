"""In-memory chat session management for the ThinkTank chat agent.

Provides a session store that tracks conversation history and pending
proposals keyed by session_id. Sessions are in-memory only (no persistence).
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime, UTC


@dataclass
class ChatMessage:
    """A single message in a chat session."""

    role: str  # "user", "assistant", "tool_use", "tool_result"
    content: str
    tool_name: str | None = None
    tool_input: dict | None = None
    tool_use_id: str | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class ChatSession:
    """A chat session with message history and pending proposals."""

    session_id: str
    messages: list[ChatMessage] = field(default_factory=list)
    pending_proposals: dict[str, dict] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class ChatSessionStore:
    """In-memory store for chat sessions."""

    def __init__(self) -> None:
        self._sessions: dict[str, ChatSession] = {}

    def create(self) -> ChatSession:
        """Create a new chat session with a generated UUID."""
        session_id = str(uuid.uuid4())
        session = ChatSession(session_id=session_id)
        self._sessions[session_id] = session
        return session

    def get(self, session_id: str) -> ChatSession | None:
        """Get an existing session by ID."""
        return self._sessions.get(session_id)

    def add_message(self, session_id: str, message: ChatMessage) -> None:
        """Append a message to a session's history."""
        session = self._sessions.get(session_id)
        if session:
            session.messages.append(message)

    def add_proposal(self, session_id: str, proposal_id: str, proposal_data: dict) -> None:
        """Store a pending proposal in a session."""
        session = self._sessions.get(session_id)
        if session:
            session.pending_proposals[proposal_id] = proposal_data

    def pop_proposal(self, session_id: str, proposal_id: str) -> dict | None:
        """Remove and return a pending proposal."""
        session = self._sessions.get(session_id)
        if session:
            return session.pending_proposals.pop(proposal_id, None)
        return None

    def get_anthropic_messages(self, session_id: str) -> list[dict]:
        """Convert session history to Anthropic API message format.

        Formats messages as required by the Anthropic messages API:
        - user messages: {"role": "user", "content": "..."}
        - assistant messages: {"role": "assistant", "content": "..." or content blocks}
        - tool_use: assistant message with tool_use content block
        - tool_result: user message with tool_result content block

        Returns:
            List of message dicts for the Anthropic API.
        """
        session = self._sessions.get(session_id)
        if not session:
            return []

        messages: list[dict] = []
        i = 0
        while i < len(session.messages):
            msg = session.messages[i]

            if msg.role == "user":
                messages.append({"role": "user", "content": msg.content})
            elif msg.role == "assistant":
                messages.append({"role": "assistant", "content": msg.content})
            elif msg.role == "tool_use":
                # Tool use is an assistant message with a content block
                messages.append({
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": msg.tool_use_id,
                            "name": msg.tool_name,
                            "input": msg.tool_input or {},
                        }
                    ],
                })
            elif msg.role == "tool_result":
                # Tool result is a user message with a tool_result content block
                messages.append({
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": msg.tool_use_id,
                            "content": msg.content,
                        }
                    ],
                })

            i += 1

        return messages

    def cleanup_old(self, max_age_hours: int = 4) -> None:
        """Remove sessions older than max_age_hours."""
        now = datetime.now(UTC)
        expired = [
            sid
            for sid, session in self._sessions.items()
            if (now - session.created_at).total_seconds() > max_age_hours * 3600
        ]
        for sid in expired:
            del self._sessions[sid]


# Module-level singleton
chat_sessions = ChatSessionStore()
