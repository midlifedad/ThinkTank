"""Canonical claim resolution: attach an observation to the claims registry.

The two-layer glue (Amir design session 2026-07-13): observations are
evidence; claims are the canonical propositions tracked over time. Every
extracted observation must land on a canonical claim -- an existing one
when it says the same thing, a new one otherwise.

Resolution is embedding-first with an LLM adjudicator only in the
ambiguity band (the expert-vetting pattern: deterministic where the
signal is clear, LLM judgment where it is not):

    cosine similarity > AUTO_ATTACH   -> attach, no LLM
    ADJUDICATE..AUTO_ATTACH           -> LLM same-proposition check
    below ADJUDICATE                  -> new canonical claim

New claims inherit parent_claim_id from the inquiry's headline claim
(the hybrid grain: fine-grained claims link upward to the question that
surfaced them).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import structlog
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from thinktank.llm.client import LLMClient
from thinktank.models.api_usage import ApiUsage
from thinktank.models.claim import Claim

logger = structlog.get_logger(__name__)

_client = LLMClient()

# Cosine similarity bands. pgvector's cosine_distance = 1 - similarity.
AUTO_ATTACH_SIMILARITY = 0.92
ADJUDICATE_SIMILARITY = 0.75
# Only the nearest few candidates are worth adjudicating.
_ANN_CANDIDATES = 3


class SameClaimVerdict(BaseModel):
    same_proposition: bool
    reasoning: str = Field(description="1-2 sentences")


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
            endpoint="claim_resolution",
            period_start=datetime.now(UTC),
            call_count=1,
            units_consumed=usage.total,
            estimated_cost_usd=cost,
        )
    )


async def _same_proposition(session: AsyncSession, text_a: str, text_b: str) -> bool:
    """LLM check: do two claim texts assert the same proposition?

    Fails toward False (new claim) -- a duplicate canonical claim is a
    recoverable merge; a wrong attach corrupts the stance history.
    """
    system = (
        "Decide whether two claim statements express the SAME underlying "
        "proposition (would a stance on one imply the same stance on the "
        "other?). Different scope, population, dosage, or direction means "
        "different propositions."
    )
    try:
        verdict, usage, _ = await _client.review(
            system, f"A: {text_a}\nB: {text_b}", SameClaimVerdict, max_tokens=768, session=session
        )
        await _record_cost(session, usage)
        return verdict.same_proposition
    except Exception:
        logger.warning("claim_adjudication_failed", exc_info=True)
        return False


async def resolve_claim(
    session: AsyncSession,
    claim_text: str,
    claim_type: str,
    embedding: list[float],
    parent_claim_id: uuid.UUID | None,
    asserted_at: datetime | None,
) -> Claim:
    """Attach-or-create: return the canonical Claim for an observation.

    Also maintains the claim's observation bookkeeping (count,
    first/last_observed_at). Caller commits.
    """
    distance = Claim.embedding.cosine_distance(embedding)
    rows = (
        await session.execute(
            select(Claim, distance.label("distance"))
            .where(Claim.embedding.is_not(None), Claim.status == "active", Claim.merged_into_id.is_(None))
            .order_by(distance)
            .limit(_ANN_CANDIDATES)
        )
    ).all()

    matched: Claim | None = None
    for candidate, dist in rows:
        similarity = 1.0 - dist
        if similarity > AUTO_ATTACH_SIMILARITY:
            matched = candidate
            break
        if similarity >= ADJUDICATE_SIMILARITY:
            if await _same_proposition(session, claim_text, candidate.proposition):
                matched = candidate
                break
        else:
            break  # ordered by distance; the rest are farther still

    if matched is None:
        matched = Claim(
            id=uuid.uuid4(),
            proposition=claim_text,
            claim_type=claim_type,
            parent_claim_id=parent_claim_id,
            embedding=embedding,
        )
        session.add(matched)

    observed = asserted_at or datetime.now(UTC)
    if matched.first_observed_at is None or observed < matched.first_observed_at:
        matched.first_observed_at = observed
    if matched.last_observed_at is None or observed > matched.last_observed_at:
        matched.last_observed_at = observed
    matched.observation_count = (matched.observation_count or 0) + 1
    return matched
