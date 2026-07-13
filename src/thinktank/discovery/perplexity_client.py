"""Perplexity deep-research client for expert-search seeding.

Stage 1 of the Expert Discovery & Vetting pipeline: ONE
``sonar-deep-research`` call per area surfaces the obvious top experts
with platform hints (YouTube/Substack/podcasts) and citations. The
output is treated strictly as CLAIMS -- Stage 2 (evidence.py + rubric)
verifies everything against structured sources before any LLM judgment.

Structured output: Perplexity's chat API supports a JSON schema response
format, so parsing is schema-enforced rather than regex-scraped.

Cost: one call per area (~$0.5-1.0 for deep research). Recorded to
api_usage (api_name='perplexity') so the A2 dashboard prices every run.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import httpx
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from thinktank.http_utils import raise_for_status_with_backoff
from thinktank.models.api_usage import ApiUsage
from thinktank.secrets import get_secret

logger = structlog.get_logger(__name__)

_API_URL = "https://api.perplexity.ai/chat/completions"
_MODEL = "sonar-deep-research"
# Deep research runs minutes server-side; generous client timeout.
_TIMEOUT = httpx.Timeout(connect=30.0, read=1800.0, write=60.0, pool=30.0)
# Flat cost estimate per deep-research request (request fee + tokens,
# ceiling). Tunable later if Perplexity exposes usage in the response.
_ESTIMATED_COST_USD = 1.00

_RESPONSE_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "schema": {
            "type": "object",
            "properties": {
                "experts": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "basis": {"type": "string"},
                            "affiliation": {"type": "string"},
                            "youtube_url": {"type": "string"},
                            "substack_url": {"type": "string"},
                            "notable_podcasts": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["name", "basis"],
                    },
                }
            },
            "required": ["experts"],
        }
    },
}

_PROMPT = """Identify up to {limit} of the most widely recognized living experts in: {area}.

Selection bar: genuine domain authorities -- leading researchers, authors of
defining books, founders/leaders of the field's key institutions. Exclude
pure commentators, journalists, and influencers whose standing rests on
audience size rather than expertise.

For each expert provide:
- name (as commonly written)
- basis: one line on why they qualify (role, landmark work, credential)
- affiliation: current primary institution or company
- youtube_url: their own channel URL if they have one (omit if none/unsure)
- substack_url: their newsletter/Substack URL (omit if none/unsure)
- notable_podcasts: up to 3 podcast titles they host or appear on repeatedly

Only include platform URLs you actually found; never guess or construct URLs."""


async def search_experts(
    session: AsyncSession,
    area: str,
    limit: int = 25,
) -> list[dict]:
    """Run one deep-research expert search for an area.

    Returns:
        List of expert claim dicts (name, basis, affiliation, platform
        hints). Empty list on any failure -- seeding degrades to the
        OpenAlex lane rather than failing the job.
    """
    api_key = await get_secret(session, "perplexity_api_key")
    if not api_key:
        logger.warning("perplexity_key_missing", hint="seed secret_perplexity_api_key in system_config")
        return []

    payload = {
        "model": _MODEL,
        "messages": [{"role": "user", "content": _PROMPT.format(area=area, limit=limit)}],
        "response_format": _RESPONSE_SCHEMA,
    }

    log = logger.bind(area=area)
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                _API_URL,
                headers={"Authorization": f"Bearer {api_key}"},
                json=payload,
            )
            raise_for_status_with_backoff(resp)
            body = resp.json()
        content = body["choices"][0]["message"]["content"]
        experts = json.loads(content).get("experts", [])
    except Exception:
        log.warning("perplexity_search_failed", exc_info=True)
        return []

    # Cost accounting (A2): prefer real usage-based pricing when the
    # response carries token counts; fall back to the flat ceiling.
    usage = body.get("usage") or {}
    total_tokens = (usage.get("prompt_tokens") or 0) + (usage.get("completion_tokens") or 0)
    session.add(
        ApiUsage(
            id=uuid.uuid4(),
            api_name="perplexity",
            endpoint="deep_research",
            period_start=datetime.now(UTC),
            call_count=1,
            units_consumed=total_tokens or None,
            estimated_cost_usd=_ESTIMATED_COST_USD,
        )
    )

    log.info("perplexity_search_complete", experts=len(experts), tokens=total_tokens)
    return experts
