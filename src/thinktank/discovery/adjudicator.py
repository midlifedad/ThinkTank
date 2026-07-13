"""LLM adjudicator for the fuzzy edges of expert vetting.

Amir direction 2026-07-12: deterministic string matching is brittle
(the longevity run auto-rejected a perfect roster on parse bugs). This
module adds LLM judgment ONLY where code genuinely can't decide --
never as an agentic loop that drives its own search.

Two on-ambiguity calls, both bounded and cost-tracked (api_usage,
api_name='anthropic', endpoint='adjudicator'):

1. resolve_entity -- when a structured-source lookup is ambiguous
   (multiple same-name authors, or a name that didn't string-match any
   result), pick the entity that matches the candidate's area/affiliation
   or return "none". Fixes the wrong-Peter-Attia class.

2. review_rejection -- when the gate is about to auto-reject a candidate
   the seed surfaced as a recognized expert AND the evidence is
   implausibly empty, decide whether the rejection is legitimate or a
   likely lookup failure that should go to a human instead. This is the
   self-check that would have flagged today's mass false-reject.

Both are skipped entirely on the easy path (single clean match, or a
rejection consistent with weak evidence), so token cost stays near zero.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import structlog
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from thinktank.llm.client import LLMClient
from thinktank.models.api_usage import ApiUsage

logger = structlog.get_logger(__name__)

_client = LLMClient()


class EntityChoice(BaseModel):
    """Which candidate entity (by index) is our person, if any."""

    choice_index: int | None  # 0-based index into options; None = no match
    confidence: float  # 0.0-1.0
    reasoning: str


class RejectionVerdict(BaseModel):
    """Is an auto-rejection legitimate, or a suspected evidence failure?"""

    legitimate: bool  # True = genuinely unqualified; False = evidence looks wrong
    reasoning: str


async def _record_cost(session: AsyncSession, usage, endpoint: str) -> None:
    from thinktank.config import get_settings

    settings = get_settings()
    cost = (
        usage.input_tokens * settings.llm_input_cost_per_mtok + usage.output_tokens * settings.llm_output_cost_per_mtok
    ) / 1_000_000.0
    session.add(
        ApiUsage(
            id=uuid.uuid4(),
            api_name="anthropic",
            endpoint=endpoint,
            period_start=datetime.now(UTC),
            call_count=1,
            units_consumed=usage.total,
            estimated_cost_usd=cost,
        )
    )


async def resolve_entity(
    session: AsyncSession,
    candidate_name: str,
    search_area: str,
    seed_basis: str | None,
    affiliation: str | None,
    source: str,
    options: list[dict],
) -> tuple[int | None, dict]:
    """Pick which of several ambiguous source results is the candidate.

    Args:
        options: compact dicts describing each candidate entity (name,
            topics/description, institution, citations) -- NOT raw API
            payloads.

    Returns:
        (chosen_index_or_None, meta) where meta records confidence and
        reasoning for the dossier. On LLM failure returns (None, ...) so
        the caller treats the source as unresolved rather than crashing.
    """
    system = (
        "You disambiguate which database record refers to a specific expert. "
        "The person trained or worked across fields over a career, so a topic "
        "that looks off-area can still be them (e.g. a longevity doctor with "
        "early cancer-immunology publications). Judge identity, not current focus. "
        "Return the index of the matching record, or null if none plausibly match."
    )
    lines = [
        f"Expert: {candidate_name}",
        f"Field of interest: {search_area}",
        f"Why they're a recognized expert: {seed_basis or 'n/a'}",
        f"Known affiliation: {affiliation or 'n/a'}",
        f"\nCandidate {source} records:",
    ]
    for i, opt in enumerate(options):
        lines.append(f"[{i}] {opt}")
    lines.append("\nWhich index is this expert? Return null if none.")

    try:
        result, usage, _ = await _client.review(system, "\n".join(lines), EntityChoice, max_tokens=512, session=session)
        await _record_cost(session, usage, "adjudicator")
    except Exception:
        logger.warning("adjudicator_resolve_failed", candidate=candidate_name, source=source, exc_info=True)
        return None, {"error": "adjudication_failed"}

    idx = result.choice_index
    if idx is not None and (idx < 0 or idx >= len(options)):
        idx = None
    return idx, {"confidence": result.confidence, "reasoning": result.reasoning, "adjudicated": True}


async def review_rejection(
    session: AsyncSession,
    candidate_name: str,
    search_area: str,
    seed_basis: str | None,
    dossier: dict,
    score: int,
) -> tuple[bool, dict]:
    """Sanity-check an auto-rejection against the seed's eminence claim.

    Returns:
        (legitimate, meta). legitimate=True keeps the auto-reject;
        False means the evidence looks broken -> caller routes to human.
        On LLM failure returns (True, ...) -- fail toward the
        deterministic decision, never silently promote.
    """
    system = (
        "A candidate expert was auto-rejected by a scoring rubric for lack of "
        "verifiable evidence. You decide whether that rejection is sound. If the "
        "candidate was surfaced as a widely-recognized authority yet the evidence "
        "shows NO citations, NO Wikipedia, and NO content, that pattern usually "
        "means an evidence-lookup failure, not a genuinely unqualified person -- "
        "mark it not-legitimate so a human can check. If the evidence merely shows "
        "modest-but-real standing below the bar, the rejection is legitimate."
    )
    evidence_summary = {
        "openalex": {k: dossier.get("openalex", {}).get(k) for k in ("found", "h_index", "cited_by_count")},
        "wikidata": {k: dossier.get("wikidata", {}).get(k) for k in ("found", "has_enwiki")},
        "openlibrary_found": dossier.get("openlibrary", {}).get("found"),
        "podcast_feeds": dossier.get("podcastindex", {}).get("appearance_feed_count"),
    }
    prompt = (
        f"Expert: {candidate_name}\n"
        f"Field: {search_area}\n"
        f"Surfaced because: {seed_basis or 'n/a'}\n"
        f"Rubric score: {score}/100\n"
        f"Evidence found: {evidence_summary}\n\n"
        "Is this rejection legitimate, or does the evidence look like a lookup failure?"
    )
    try:
        result, usage, _ = await _client.review(system, prompt, RejectionVerdict, max_tokens=400, session=session)
        await _record_cost(session, usage, "adjudicator")
    except Exception:
        logger.warning("adjudicator_review_failed", candidate=candidate_name, exc_info=True)
        return True, {"error": "adjudication_failed"}

    return result.legitimate, {"reasoning": result.reasoning, "adjudicated": True}
