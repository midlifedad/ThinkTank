"""Anthropic API client wrapper for LLM Supervisor.

Thin async wrapper around AsyncAnthropic that provides a review() method
returning structured output via the tool_use pattern.

Uses tool_use approach: defines a tool with the Pydantic JSON schema,
forces tool_choice to that tool, then parses the tool_use block's input
into the Pydantic model. This is universally supported across SDK versions.

Spec reference: Section 8.1 (LLM Supervisor client).
"""

import time
from typing import NamedTuple

from anthropic import AsyncAnthropic
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from thinktank.config import get_settings
from thinktank.secrets import get_secret


class LLMUsage(NamedTuple):
    """Token usage for one LLM call, split for cost accounting (A2)."""

    input_tokens: int
    output_tokens: int

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens


class LLMClient:
    """Thin wrapper for Anthropic API calls with project-specific defaults."""

    def __init__(self) -> None:
        self._client: AsyncAnthropic | None = None
        self.model = get_settings().llm_model

    async def _get_client(self, session: AsyncSession) -> AsyncAnthropic:
        """Get or create Anthropic client with DB-backed API key."""
        api_key = await get_secret(session, "anthropic_api_key")
        if not api_key:
            raise ValueError("Anthropic API key not configured — set via Admin > API Keys")
        return AsyncAnthropic(
            api_key=api_key,
            max_retries=2,
            timeout=120.0,
        )

    async def review(
        self,
        system_prompt: str,
        user_prompt: str,
        response_schema: type[BaseModel],
        max_tokens: int = 4096,
        session: AsyncSession | None = None,
    ) -> tuple[BaseModel, LLMUsage, int]:
        """Call Claude and return (parsed_output, usage, duration_ms).

        Uses tool_use pattern for structured output:
        1. Creates a tool definition from the Pydantic model's JSON schema
        2. Forces tool_choice to use that tool
        3. Parses the tool_use block's input into the Pydantic model

        Args:
            system_prompt: System instructions for the LLM.
            user_prompt: User message with context and task.
            response_schema: Pydantic model class for the expected response.
            max_tokens: Maximum tokens for the response.
            session: Database session for API key lookup.

        Returns:
            Tuple of (parsed_response, LLMUsage, duration_ms). LLMUsage
            carries the input/output token split for cost accounting;
            use ``usage.total`` for the combined count.

        Raises:
            anthropic.APIConnectionError: On network issues.
            anthropic.RateLimitError: When rate limited (after max_retries).
            anthropic.APIStatusError: On API errors.
            ValueError: If no API key is configured and no session provided.
        """
        start = time.monotonic()

        tool_definition = {
            "name": "structured_output",
            "description": "Return the structured response",
            "input_schema": response_schema.model_json_schema(),
        }

        client = await self._get_client(session) if session else self._client
        if client is None:
            raise ValueError("Anthropic API key not configured — set via Admin > API Keys")

        response = await client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
            tools=[tool_definition],
            tool_choice={"type": "tool", "name": "structured_output"},
        )

        # Find the tool_use content block and parse it.
        # INTEGRATIONS-REVIEW M-02 (T6.11): use ``next(iter, None)`` so a
        # refusal / safety response that contains only text (or an empty
        # content list) raises a typed ``ValueError`` instead of letting
        # a raw ``StopIteration`` escape as ``RuntimeError``.
        tool_use_block = next(
            (block for block in response.content if block.type == "tool_use"),
            None,
        )
        if tool_use_block is None:
            raise ValueError("Claude response did not include a tool_use block (possibly a refusal or safety response)")
        # Truncation guard (2026-07-13): when generation hits max_tokens
        # MID-TOOL-CALL, the API returns the incomplete input as an empty
        # or partial dict -- which then either fails validation with a
        # misleading "field required" error or, worse, VALIDATES as a
        # legitimate empty result when the schema has defaults (both live
        # roster critiques silently returned {} this way). Surface it as
        # what it is so callers raise their cap instead of chasing ghosts.
        if response.stop_reason == "max_tokens":
            raise ValueError(
                f"LLM response truncated at max_tokens={max_tokens}: tool input incomplete -- "
                f"raise the max_tokens for this call (schema: {response_schema.__name__})"
            )
        parsed_result = response_schema.model_validate(tool_use_block.input)

        duration_ms = int((time.monotonic() - start) * 1000)
        usage = LLMUsage(
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )

        return parsed_result, usage, duration_ms
