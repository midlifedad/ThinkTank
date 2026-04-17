"""Streaming agent response via Anthropic API.

Streams SSE-formatted events for text deltas, tool calls, proposals,
and tool results. Handles the tool-use loop (up to 5 iterations).
"""

import json
import uuid
from collections.abc import AsyncGenerator

from anthropic import AsyncAnthropic
from sqlalchemy.ext.asyncio import AsyncSession

from thinktank.agent.session import ChatMessage, chat_sessions
from thinktank.agent.system_prompt import build_chat_system_prompt
from thinktank.agent.tools import AGENT_TOOLS, execute_tool
from thinktank.secrets import get_secret


async def stream_agent_response(
    session_id: str,
    user_message: str,
    db_session: AsyncSession,
) -> AsyncGenerator[str, None]:
    """Stream an agent response as SSE-formatted events.

    Handles:
    - Text delta streaming
    - Tool use (query_database, propose_action)
    - Tool use loop (up to 5 iterations)
    - Error handling

    Args:
        session_id: Chat session ID.
        user_message: The user's message text.
        db_session: Database session for tool execution.

    Yields:
        SSE-formatted strings (data: {...}\\n\\n).
    """
    try:
        # Get or create session
        session = chat_sessions.get(session_id)
        if not session:
            session = chat_sessions.create()
            session_id = session.session_id

        # Add user message to session
        chat_sessions.add_message(
            session_id,
            ChatMessage(role="user", content=user_message),
        )

        # Get Anthropic client
        api_key = await get_secret(db_session, "anthropic_api_key")
        if not api_key:
            yield _sse_event({"type": "error", "message": "Anthropic API key not configured"})
            return
        client = AsyncAnthropic(api_key=api_key, max_retries=2, timeout=120.0)

        # Build messages for API call
        messages = chat_sessions.get_anthropic_messages(session_id)
        system_prompt = build_chat_system_prompt()

        # Tool use loop (max 5 iterations)
        for _iteration in range(5):
            full_text = ""
            tool_use_blocks: list[dict] = []

            async with client.messages.stream(
                model="claude-sonnet-4-20250514",
                system=system_prompt,
                messages=messages,
                tools=AGENT_TOOLS,
                max_tokens=4096,
            ) as stream:
                async for event in stream:
                    if event.type == "content_block_delta":
                        if hasattr(event.delta, "text"):
                            full_text += event.delta.text
                            yield _sse_event({"type": "text_delta", "text": event.delta.text})

                # Get the final message for tool use blocks
                final_message = await stream.get_final_message()

            # Extract tool use blocks from the final message
            for block in final_message.content:
                if block.type == "tool_use":
                    tool_use_blocks.append(
                        {
                            "id": block.id,
                            "name": block.name,
                            "input": block.input,
                        }
                    )

            # Record assistant message in session
            if full_text:
                chat_sessions.add_message(
                    session_id,
                    ChatMessage(role="assistant", content=full_text),
                )

            # If no tool use, we're done
            if final_message.stop_reason != "tool_use" or not tool_use_blocks:
                break

            # Process tool calls
            for tool_block in tool_use_blocks:
                tool_name = tool_block["name"]
                tool_input = tool_block["input"]
                tool_use_id = tool_block["id"]

                # Record tool use in session
                chat_sessions.add_message(
                    session_id,
                    ChatMessage(
                        role="tool_use",
                        content="",
                        tool_name=tool_name,
                        tool_input=tool_input,
                        tool_use_id=tool_use_id,
                    ),
                )

                # Execute the tool
                tool_result = await execute_tool(tool_name, tool_input, db_session)

                if tool_name == "propose_action" and "proposal" in tool_result:
                    # Generate proposal ID and store in session
                    proposal_id = str(uuid.uuid4())
                    proposal = tool_result["proposal"]
                    chat_sessions.add_proposal(session_id, proposal_id, proposal)
                    yield _sse_event(
                        {
                            "type": "proposal",
                            "proposal_id": proposal_id,
                            "action_type": proposal["action_type"],
                            "target": proposal["target"],
                            "details": proposal["details"],
                            "explanation": proposal["explanation"],
                        }
                    )
                elif tool_name == "query_database":
                    yield _sse_event(
                        {
                            "type": "tool_result",
                            "tool": "query_database",
                            "result": tool_result,
                        }
                    )

                # Record tool result in session
                result_str = json.dumps(tool_result)
                chat_sessions.add_message(
                    session_id,
                    ChatMessage(
                        role="tool_result",
                        content=result_str,
                        tool_name=tool_name,
                        tool_use_id=tool_use_id,
                    ),
                )

            # Rebuild messages for continuation
            messages = chat_sessions.get_anthropic_messages(session_id)

        yield _sse_event({"type": "done"})

    except Exception as e:
        yield _sse_event({"type": "error", "message": str(e)})


def _sse_event(data: dict) -> str:
    """Format a dict as an SSE event string."""
    return f"data: {json.dumps(data)}\n\n"
