"""LLM domain-fit assessment for expert vetting.

The deterministic rubric measures GENERAL eminence (citations, Wikipedia,
books, content) -- signals that transfer across domains. Domain
centrality does not transfer and exists in no countable evidence: the
2026-07-13 "AI coding and agentic engineering" search promoted eminent
ML academics while auto-rejecting the people who literally created the
field's canonical artifacts (design doc: docs/plans/
2026-07-13-dynamic-expert-standing.md).

One schema-enforced LLM call per candidate answers the one question the
rubric cannot: how central is this person to THIS area? The result is a
ROUTING signal (rescue path + judge context), never a replacement for
the deterministic score. Fail-open: on any failure vetting proceeds
exactly as before, without a fit assessment.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Literal

import structlog
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from thinktank.llm.client import LLMClient
from thinktank.models.api_usage import ApiUsage

logger = structlog.get_logger(__name__)

_client = LLMClient()


class DomainFitAssessment(BaseModel):
    centrality: Literal["core", "adjacent", "peripheral"] = Field(
        description=(
            "core = this area is what the person is known FOR; "
            "adjacent = genuine authority in a neighboring field with real work in this area; "
            "peripheral = eminent elsewhere, incidental to this area"
        )
    )
    fit_score: int = Field(ge=0, le=20, description="0-20: centrality of this person to this specific area")
    reasoning: str = Field(description="2-3 sentences citing the specific evidence")


async def _record_cost(session: AsyncSession, usage) -> None:
    from thinktank.config import get_settings

    settings = get_settings()
    cost = (
        usage.input_tokens * settings.llm_input_cost_per_mtok + usage.output_tokens * settings.llm_output_cost_per_mtok
    ) / 1_000_000.0
    session.add(
        ApiUsage(
            id=uuid.uuid4(),
            api_name="anthropic",
            endpoint="domain_fit",
            period_start=datetime.now(UTC),
            call_count=1,
            units_consumed=usage.total,
            estimated_cost_usd=cost,
        )
    )


def _dossier_facts(dossier: dict) -> str:
    """Compact the dossier to the identity facts the fit question needs."""
    openalex = dossier.get("openalex") or {}
    wikidata = dossier.get("wikidata") or {}
    books = dossier.get("openlibrary") or {}
    podcasts = dossier.get("podcastindex") or {}
    seed = dossier.get("seed_claim") or {}
    lines = []
    if seed.get("basis"):
        lines.append(f"Seed basis: {seed['basis']}")
    if seed.get("affiliation"):
        lines.append(f"Affiliation: {seed['affiliation']}")
    if wikidata.get("description"):
        lines.append(f"Wikidata: {wikidata['description']}")
    if openalex.get("found"):
        lines.append(
            f"Scholarship: h-index {openalex.get('h_index')}, "
            f"{openalex.get('works_count')} works, fields: {openalex.get('topics') or openalex.get('concepts')}"
        )
    titles = [b.get("title") for b in (books.get("books") or [])[:5] if b.get("title")]
    if titles:
        lines.append(f"Books: {titles}")
    episode_titles = [e.get("title") for e in (podcasts.get("items") or [])[:5] if e.get("title")]
    if episode_titles:
        lines.append(f"Recent podcast appearances: {episode_titles}")
    return "\n".join(lines) or "(no structured evidence)"


async def assess_domain_fit(
    session: AsyncSession,
    name: str,
    area: str,
    dossier: dict,
) -> dict | None:
    """Assess a candidate's centrality to an area. None on failure.

    Returns a dict (not the model) so it stores directly into the
    evidence JSONB and survives re-vets like the rest of the dossier.
    """
    system = (
        "You assess how central a person is to a SPECIFIC domain of expertise. "
        "General eminence is not fit: a Nobel-class figure in an adjacent field "
        "is 'peripheral' here unless this area is genuinely their work. "
        "Conversely, someone with thin academic credentials who created the "
        "area's defining tools, essays, or frameworks is 'core'. Judge from "
        "what the person is actually known for."
    )
    prompt = f"Area: {area}\nPerson: {name}\nEvidence:\n{_dossier_facts(dossier)}"
    try:
        verdict, usage, _ = await _client.review(system, prompt, DomainFitAssessment, max_tokens=400, session=session)
        await _record_cost(session, usage)
    except Exception:
        logger.warning("domain_fit_failed", name=name, area=area, exc_info=True)
        return None
    logger.info("domain_fit_assessed", name=name, area=area, centrality=verdict.centrality, score=verdict.fit_score)
    return {
        "centrality": verdict.centrality,
        "fit_score": verdict.fit_score,
        "reasoning": verdict.reasoning,
        "assessed_at": datetime.now(UTC).isoformat(),
    }
