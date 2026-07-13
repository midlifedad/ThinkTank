"""LLM claims extraction for the inquiry engine.

Three bounded calls, all schema-enforced through LLMClient and all
cost-tracked (api_usage endpoint='claims_extraction'):

1. propositionize -- once per inquiry: question -> stance-neutral
   headline proposition + claim type.
2. extract_observations -- per expert per evidence bundle: pull atomic,
   quoted claims relevant to the question. GROUNDING IS PROGRAMMATIC:
   every returned quote is located in the evidence text by code; a quote
   that can't be found is dropped (anti-hallucination -- same discipline
   as the vetting rubric).
3. resolve_position -- per expert: synthesize their observations into
   the REQUIRED stance on the headline question (the stance-matrix
   cell), 'unknown' when the evidence doesn't address it.
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

ClaimType = Literal["factual", "prediction", "opinion", "practice", "recommendation"]
Stance = Literal["asserts", "denies", "hedges", "questions", "reports"]


class Proposition(BaseModel):
    proposition: str = Field(description="Stance-neutral restatement of the question as a proposition")
    claim_type: ClaimType


class ExtractedClaim(BaseModel):
    claim_text: str = Field(description="Atomic, self-contained restatement of what the expert asserted")
    claim_type: ClaimType
    stance_on_question: Stance = Field(description="The expert's stance toward the inquiry question")
    confidence: Literal["asserted", "speculated", "reported"] = Field(
        description="How firmly the expert spoke: direct assertion, speculation/hedge, or reporting others"
    )
    quote: str = Field(description="VERBATIM quote from the evidence text supporting this claim (copy exactly)")
    topics: list[str] = Field(default_factory=list, description="1-3 short topic tags")


class ExtractionResponse(BaseModel):
    # Defaulted, not required: "extract nothing if the evidence does not
    # address the question" is a legitimate outcome, and the model signals
    # it by calling the tool with empty input ({}). A required field would
    # turn that valid no-op into a validation crash that fails the whole
    # inquiry job. The empty default also drops `claims` from the tool's
    # required-schema, so the model is explicitly permitted to return none.
    claims: list[ExtractedClaim] = Field(default_factory=list)


class PositionResponse(BaseModel):
    stance: Literal["asserts", "denies", "hedges", "questions", "reports", "unknown"]
    summary: str = Field(description="2-3 sentence summary of the expert's position on the question")


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
            endpoint="claims_extraction",
            period_start=datetime.now(UTC),
            call_count=1,
            units_consumed=usage.total,
            estimated_cost_usd=cost,
        )
    )


async def propositionize(session: AsyncSession, question: str) -> Proposition:
    """Turn an inquiry question into the headline canonical proposition."""
    system = (
        "Restate the question as a single stance-neutral proposition an expert "
        "could assert, deny, or hedge on, and classify its claim type."
    )
    result, usage, _ = await _client.review(
        system, f"Question: {question}", Proposition, max_tokens=300, session=session
    )
    await _record_cost(session, usage)
    return result


def ground_quote(quote: str, evidence_text: str) -> tuple[int, int] | None:
    """Locate a quote in evidence text; (start, end) offsets or None.

    Exact match first; then a whitespace-normalized scan that maps back
    to original offsets (transcripts and LLM output disagree about
    whitespace more than about words).
    """
    if not quote.strip():
        return None  # str.find("") is 0 -- an empty quote must not ground
    idx = evidence_text.find(quote)
    if idx >= 0:
        return idx, idx + len(quote)

    def _tokens_with_offsets(text: str) -> list[tuple[str, int, int]]:
        out, i = [], 0
        for word in text.split():
            start = text.index(word, i)
            out.append((word.lower(), start, start + len(word)))
            i = start + len(word)
        return out

    quote_tokens = [w.lower() for w in quote.split()]
    if not quote_tokens:
        return None
    ev = _tokens_with_offsets(evidence_text)
    ev_words = [w for w, _, _ in ev]
    n = len(quote_tokens)
    for i in range(len(ev_words) - n + 1):
        if ev_words[i : i + n] == quote_tokens:
            return ev[i][1], ev[i + n - 1][2]
    return None


async def extract_observations(
    session: AsyncSession,
    question: str,
    expert_name: str,
    evidence_text: str,
    evidence_kind: str,
) -> tuple[list[ExtractedClaim], int]:
    """Extract grounded claims one expert made in one evidence bundle.

    Returns (grounded_claims, dropped_ungrounded_count). Quotes are
    verified against evidence_text by code -- claims whose quotes can't
    be located are dropped, not stored.
    """
    system = (
        "You extract what a SPECIFIC EXPERT has asserted, relevant to a question. "
        "Only extract claims made by the named expert (in transcripts, their own "
        "speaker turns; in articles, their quoted or authored statements) -- never "
        "claims by hosts or third parties. Every claim needs a VERBATIM quote "
        "copied exactly from the evidence. Extract nothing if the evidence does "
        "not address the question."
    )
    prompt = (
        f"Question: {question}\n"
        f"Expert: {expert_name}\n"
        f"Evidence ({evidence_kind}):\n---\n{evidence_text}\n---\n"
        "Extract the expert's claims relevant to the question."
    )
    result, usage, _ = await _client.review(system, prompt, ExtractionResponse, max_tokens=2048, session=session)
    await _record_cost(session, usage)

    grounded: list[ExtractedClaim] = []
    dropped = 0
    for claim in result.claims:
        if ground_quote(claim.quote, evidence_text) is not None:
            grounded.append(claim)
        else:
            dropped += 1
    if dropped:
        logger.warning("ungrounded_claims_dropped", expert=expert_name, dropped=dropped, kept=len(grounded))
    return grounded, dropped


async def resolve_position(
    session: AsyncSession,
    question: str,
    expert_name: str,
    observations: list[dict],
) -> PositionResponse:
    """Synthesize an expert's REQUIRED position on the headline question."""
    if not observations:
        return PositionResponse(stance="unknown", summary="No relevant statements found in corpus or web evidence.")
    system = (
        "Given an expert's extracted statements about a question, resolve their "
        "overall position. 'unknown' only if the statements genuinely don't "
        "address the question."
    )
    lines = [f"Question: {question}", f"Expert: {expert_name}", "Statements:"]
    for obs in observations[:30]:
        lines.append(f"- [{obs['stance']}/{obs.get('confidence')}] {obs['claim_text']}")
    result, usage, _ = await _client.review(system, "\n".join(lines), PositionResponse, max_tokens=500, session=session)
    await _record_cost(session, usage)
    return result
