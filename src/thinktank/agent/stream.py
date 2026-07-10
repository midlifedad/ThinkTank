"""Streaming agent response via Anthropic API.

Streams SSE-formatted events for text deltas, tool calls, proposals,
and tool results. Handles the tool-use loop (up to 5 iterations).
"""

import json
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime

import structlog
from anthropic import AsyncAnthropic
from sqlalchemy.ext.asyncio import AsyncSession

from thinktank.agent.session import ChatMessage, chat_sessions
from thinktank.agent.system_prompt import build_chat_system_prompt
from thinktank.agent.tools import AGENT_TOOLS, execute_tool
from thinktank.config import get_settings
from thinktank.models.api_usage import ApiUsage
from thinktank.secrets import get_secret

logger = structlog.get_logger(__name__)


async def _record_chat_usage(
    db_session: AsyncSession,
    call_count: int,
    input_tokens: int,
    output_tokens: int,
) -> None:
    """Record one chat exchange's Anthropic usage into api_usage (A2).

    Chat calls bypass LLMClient/llm_reviews, so without this they were
    invisible to cost tracking. Written per-exchange (endpoint='chat')
    rather than via the hourly rollup; dashboards sum estimated_cost_usd
    across endpoints. Failures are logged, never raised -- cost accounting
    must not break a user-facing stream.
    """
    if call_count == 0:
        return
    try:
        settings = get_settings()
        cost = (
            input_tokens * settings.llm_input_cost_per_mtok + output_tokens * settings.llm_output_cost_per_mtok
        ) / 1_000_000.0
        db_session.add(
            ApiUsage(
                id=uuid.uuid4(),
                api_name="anthropic",
                endpoint="chat",
                period_start=datetime.now(UTC),
                call_count=call_count,
                units_consumed=input_tokens + output_tokens,
                estimated_cost_usd=cost,
            )
        )
        await db_session.commit()
    except Exception:
        logger.exception("chat_usage_recording_failed")


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

        # Usage accumulators across the tool-use loop (A2 cost tracking)
        api_calls = 0
        total_input_tokens = 0
        total_output_tokens = 0

        # Tool use loop (max 5 iterations)
        for _iteration in range(5):
            full_text = ""
            tool_use_blocks: list[dict] = []

            async with client.messages.stream(
                # Config-driven model (was hardcoded to a deprecated ID --
                # the INTEGRATIONS L-01 fix covered LLMClient but missed
                # this call site).
                model=get_settings().llm_model,
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

            api_calls += 1
            total_input_tokens += final_message.usage.input_tokens
            total_output_tokens += final_message.usage.output_tokens

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

        await _record_chat_usage(db_session, api_calls, total_input_tokens, total_output_tokens)

        yield _sse_event({"type": "done"})

    except Exception as e:
        yield _sse_event({"type": "error", "message": str(e)})


def _sse_event(data: dict) -> str:
    """Format a dict as an SSE event string."""
    return f"data: {json.dumps(data)}\n\n"
