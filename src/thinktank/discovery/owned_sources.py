"""Discover an expert's OWNED content channels (Web-Lane Hardening W3.1).

The rapamycin inquiry showed the corpus has almost no content attributed
to the longevity roster -- it succeeded on the web lane, not on ingested
transcripts. Passive guest-detection only catches experts where they
appear on already-tracked feeds; it never goes and finds an expert's OWN
YouTube channel, podcast, Substack, or site.

This module is the discovery half: an Exa search for the person's online
presence, then an LLM that picks out the channels it is CONFIDENT the
person actually owns (identity is the hard part -- name collisions,
impersonators, fan channels). The handler registers what comes back as
owned sources (approval-gated) so the existing ingestion picks them up.

Owned YouTube channels and podcast feeds flow through the existing
fetch_youtube_channel / fetch_podcast_feed ingestion once approved.
Website/Substack ingestion (new content paths) and OpenAlex OA paper
ingestion (the academic-expert fix) are W3.2.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from thinktank.discovery.exa_client import exa_search
from thinktank.llm.client import LLMClient
from thinktank.models.api_usage import ApiUsage

_client = LLMClient()

# One search returns the person's presence across channel types; more
# results = better identity disambiguation for the LLM.
_SEARCH_RESULTS = 10


class OwnedChannels(BaseModel):
    """Channels the LLM is confident the NAMED person owns. All optional."""

    youtube_channel_url: str | None = Field(
        default=None, description="URL of the person's OWN YouTube channel (not a single video, not someone else's)"
    )
    podcast_url: str | None = Field(
        default=None, description="URL of a podcast the person HOSTS (not one they merely guested on)"
    )
    substack_url: str | None = Field(default=None, description="URL of the person's own Substack/newsletter")
    website_url: str | None = Field(default=None, description="URL of the person's personal or lab website")
    reasoning: str = Field(description="1-2 sentences on the identity evidence")


async def find_owned_channels(session: AsyncSession, name: str, area: str | None = None) -> OwnedChannels | None:
    """Discover the channels an expert owns. None on no-signal / failure.

    Fail-open: any Exa/LLM failure returns None and the caller simply
    registers nothing (no owned sources discovered this run).
    """
    context = f" (known for: {area})" if area else ""
    query = f"{name}{context} official YouTube channel, podcast, Substack, personal website"
    results = await exa_search(session, query, _SEARCH_RESULTS)
    if not results:
        return None

    listing = "\n".join(f"- {r.url} | {r.title or ''}" + (f" | {r.author}" if r.author else "") for r in results)
    system = (
        "You identify the online channels a SPECIFIC person OWNS, from search "
        "results. Owned means the person controls it: their own YouTube "
        "channel, a podcast they host, their Substack, their personal/lab "
        "site. NOT: single videos, podcasts they only guested on, fan pages, "
        "namesakes, or third-party coverage. Return a URL for a field ONLY if "
        "the results give clear evidence the named person owns it; otherwise "
        "leave it null. Prefer the channel/home URL over a deep link."
    )
    prompt = f"Person: {name}{context}\n\nSearch results:\n{listing}"
    try:
        channels, usage, _ = await _client.review(system, prompt, OwnedChannels, max_tokens=600, session=session)
    except Exception:
        return None
    _record_cost(session, usage)
    return channels


def _record_cost(session: AsyncSession, usage) -> None:
    from thinktank.config import get_settings

    settings = get_settings()
    cost = (
        usage.input_tokens * settings.llm_input_cost_per_mtok + usage.output_tokens * settings.llm_output_cost_per_mtok
    ) / 1_000_000.0
    session.add(
        ApiUsage(
            id=uuid.uuid4(),
            api_name="anthropic",
            endpoint="owned_source_discovery",
            period_start=datetime.now(UTC),
            call_count=1,
            units_consumed=usage.total,
            estimated_cost_usd=cost,
        )
    )
