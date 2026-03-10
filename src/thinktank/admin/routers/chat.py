"""Chat API router for the ThinkTank admin agent.

Provides SSE streaming chat, action confirmation, and history endpoints.
The agent can query the database and propose mutations for user confirmation.
"""

import json

from fastapi import APIRouter, Depends, Form, HTTPException, Query
from sse_starlette.sse import EventSourceResponse
from sqlalchemy.ext.asyncio import AsyncSession

from thinktank.admin.dependencies import get_session
from thinktank.agent.session import chat_sessions
from thinktank.agent.stream import stream_agent_response
from thinktank.agent.tools import execute_confirmed_action
from thinktank.database import async_session_factory

router = APIRouter(prefix="/admin/chat", tags=["chat"])


@router.post("/send")
async def chat_send(
    message: str = Form(...),
    session_id: str = Form(""),
):
    """Send a message and stream the agent response via SSE.

    If no session_id is provided, a new session is created and its ID
    is sent as the first SSE event.
    """
    new_session = False
    if not session_id:
        session = chat_sessions.create()
        session_id = session.session_id
        new_session = True

    async def event_generator():
        # Create a DB session that lives for the duration of streaming
        async with async_session_factory() as db_session:
            if new_session:
                yield json.dumps({"type": "session_init", "session_id": session_id})

            async for event_data in stream_agent_response(session_id, message, db_session):
                yield json.dumps(event_data)

    return EventSourceResponse(
        event_generator(),
        media_type="text/event-stream",
    )


@router.post("/confirm/{proposal_id}")
async def chat_confirm(
    proposal_id: str,
    session_id: str = Form(...),
    db_session: AsyncSession = Depends(get_session),
):
    """Execute a previously proposed action after user confirmation.

    Pops the proposal from the session store and executes it.
    Returns JSON with the action result.
    """
    proposal = chat_sessions.pop_proposal(session_id, proposal_id)
    if proposal is None:
        raise HTTPException(
            status_code=404,
            detail="Proposal not found or already executed",
        )

    result = await execute_confirmed_action(
        proposal["action_type"],
        proposal["details"],
        db_session,
    )
    return result


@router.get("/history")
async def chat_history(
    session_id: str = Query(...),
):
    """Get chat history for a session.

    Returns filtered message history (user and assistant text messages
    plus proposals). Tool-internal messages are excluded.
    """
    session = chat_sessions.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    messages = []
    for msg in session.messages:
        if msg.role in ("user", "assistant"):
            entry = {
                "role": msg.role,
                "content": msg.content,
                "timestamp": msg.timestamp.isoformat(),
            }
            messages.append(entry)
        elif msg.role == "tool_use" and msg.tool_name == "propose_action":
            # Include proposals as a special entry
            entry = {
                "role": "assistant",
                "content": "",
                "timestamp": msg.timestamp.isoformat(),
                "proposal": msg.tool_input,
            }
            messages.append(entry)

    return messages
