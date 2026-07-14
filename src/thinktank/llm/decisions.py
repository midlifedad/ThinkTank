"""Decision application logic for LLM review outcomes.

Dispatches review decisions to entity-specific handlers that update
approval_status, link llm_review_id, and handle candidate-to-thinker
promotion.

Uses the _now() pattern from queue/claim.py for timezone-naive datetimes.

Spec reference: Section 8.4 (decision application).
"""

import re
import uuid
from datetime import UTC, datetime

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from thinktank.llm.schemas import (
    CandidateReviewResponse,
    SourceApprovalResponse,
    ThinkerApprovalResponse,
)
from thinktank.models.candidate import CandidateThinker
from thinktank.models.job import Job
from thinktank.models.source import Source, SourceThinker
from thinktank.models.thinker import Thinker


def _now() -> datetime:
    """Return current UTC time as timezone-naive datetime."""
    return datetime.now(UTC)


def _slugify(name: str) -> str:
    """Convert a name to a URL-safe slug.

    Lowercase, replace non-alphanumeric with hyphens, collapse multiples.
    """
    slug = name.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")
    return slug


async def _unique_thinker_slug(session: AsyncSession, name: str) -> str:
    """Return a slug that does not collide with any existing ``thinkers.slug``.

    Two candidates with the same normalized name (e.g. "Jane Smith" surfacing
    a second time from a different source) would otherwise race on the
    ``thinkers.slug`` UNIQUE constraint and raise IntegrityError inside the
    LLM commit path. Appends ``-2``, ``-3``, ... until the slug is free.
    """
    base = _slugify(name)
    candidate = base
    suffix = 2
    while True:
        existing = await session.execute(select(Thinker.id).where(Thinker.slug == candidate))
        if existing.scalar_one_or_none() is None:
            return candidate
        candidate = f"{base}-{suffix}"
        suffix += 1


async def apply_decision(
    session: AsyncSession,
    review_type: str,
    target_id: uuid.UUID,
    pending_job_id: uuid.UUID | None,
    result: BaseModel,
    review_id: uuid.UUID,
) -> None:
    """Dispatch a review decision to the correct entity handler.

    After applying the entity-specific decision, updates the pending
    job's llm_review_id and status if a pending_job_id is provided.

    Args:
        session: Active database session.
        review_type: One of "thinker_approval", "source_approval", "candidate_review".
        target_id: UUID of the entity being reviewed.
        pending_job_id: UUID of the job awaiting this review (if any).
        result: Parsed Pydantic response from the LLM.
        review_id: UUID of the LLMReview row for audit trail linking.
    """
    # Determine if the decision allows further processing
    decision_allows_processing = True

    if review_type == "thinker_approval":
        await apply_thinker_decision(session, target_id, result)  # type: ignore[arg-type]
        decision_allows_processing = result.decision in ("approved", "approved_with_modifications")

    elif review_type == "source_approval":
        await apply_source_decision(session, target_id, result)  # type: ignore[arg-type]
        decision_allows_processing = result.decision in ("approved", "approved_with_modifications")

    elif review_type == "candidate_review":
        await apply_candidate_decision(session, target_id, result, review_id)  # type: ignore[arg-type]
        decision_allows_processing = result.decision == "approved"

    # Update pending job if provided
    if pending_job_id is not None:
        job = await session.get(Job, pending_job_id)
        if job is not None:
            job.llm_review_id = review_id
            if decision_allows_processing:
                job.status = "pending"  # Ready for processing
            else:
                job.status = "done"  # No further action needed

    await session.flush()


async def apply_thinker_decision(
    session: AsyncSession,
    thinker_id: uuid.UUID,
    result: ThinkerApprovalResponse,
) -> None:
    """Apply a thinker approval decision.

    Args:
        session: Active database session.
        thinker_id: UUID of the thinker.
        result: Parsed approval response.
    """
    thinker = await session.get(Thinker, thinker_id)

    if result.decision == "approved":
        thinker.approval_status = "approved"
    elif result.decision == "rejected":
        thinker.approval_status = "rejected_by_llm"
    elif result.decision == "approved_with_modifications":
        thinker.approval_status = "approved"
        # Apply modifications to the thinker
        if result.modifications:
            if "approved_backfill_days" in result.modifications:
                thinker.approved_backfill_days = result.modifications["approved_backfill_days"]
            if "approved_source_types" in result.modifications:
                thinker.approved_source_types = result.modifications["approved_source_types"]
    elif result.decision == "escalate_to_human":
        thinker.approval_status = "pending_human"

    # Trigger discovery pipeline for approved thinkers
    if thinker.approval_status == "approved":
        discover_job = Job(
            id=uuid.uuid4(),
            job_type="discover_thinker",
            payload={"thinker_id": str(thinker_id)},
            priority=5,
            status="pending",
        )
        session.add(discover_job)

        # Trigger retroactive scan of cataloged episodes for newly approved thinker
        rescan_job = Job(
            id=uuid.uuid4(),
            job_type="rescan_cataloged_for_thinker",
            payload={
                "thinker_id": str(thinker_id),
                "thinker_name": thinker.name,
            },
            priority=4,
            status="pending",
        )
        session.add(rescan_job)

    await session.flush()


async def apply_source_decision(
    session: AsyncSession,
    source_id: uuid.UUID,
    result: SourceApprovalResponse,
) -> None:
    """Apply a source approval decision.

    Args:
        session: Active database session.
        source_id: UUID of the source.
        result: Parsed approval response.
    """
    source = await session.get(Source, source_id)
    if source is None:
        return

    if result.decision == "approved":
        source.approval_status = "approved"
        if result.approved_backfill_days is not None:
            source.approved_backfill_days = result.approved_backfill_days
    elif result.decision == "rejected":
        source.approval_status = "rejected_by_llm"
    elif result.decision == "approved_with_modifications":
        source.approval_status = "approved"
        if result.approved_backfill_days is not None:
            source.approved_backfill_days = result.approved_backfill_days
        if result.modifications and "approved_backfill_days" in result.modifications:
            source.approved_backfill_days = result.modifications["approved_backfill_days"]
    elif result.decision == "escalate_to_human":
        source.approval_status = "pending_human"

    # Trigger feed fetch for approved sources
    if source.approval_status == "approved":
        fetch_payload = {"source_id": str(source_id)}
        # Add guest filtering if a thinker has a guest_appearance relationship
        guest_result = await session.execute(
            select(SourceThinker.thinker_id)
            .where(
                SourceThinker.source_id == source.id,
                SourceThinker.relationship_type == "guest_appearance",
            )
            .limit(1)
        )
        guest_thinker_id = guest_result.scalar_one_or_none()
        if guest_thinker_id is not None:
            fetch_payload["guest_filter_thinker_id"] = str(guest_thinker_id)
        fetch_job = Job(
            id=uuid.uuid4(),
            job_type="fetch_podcast_feed",
            payload=fetch_payload,
            priority=3,
            status="pending",
        )
        session.add(fetch_job)

    await session.flush()


async def apply_candidate_decision(
    session: AsyncSession,
    candidate_id: uuid.UUID,
    result: CandidateReviewResponse,
    review_id: uuid.UUID,
) -> None:
    """Apply a candidate thinker review decision.

    Args:
        session: Active database session.
        candidate_id: UUID of the candidate.
        result: Parsed review response.
        review_id: UUID of the LLMReview for audit trail.
    """
    candidate = await session.get(CandidateThinker, candidate_id)

    # Always set review metadata
    candidate.llm_review_id = review_id
    candidate.reviewed_by = "llm"
    candidate.reviewed_at = _now()

    if result.decision == "approved":
        await promote_candidate_to_thinker(session, candidate, result)
    elif result.decision == "rejected":
        candidate.status = "rejected"
    elif result.decision == "duplicate":
        candidate.status = "rejected_duplicate"
    elif result.decision == "need_more_appearances":
        candidate.status = "needs_more_data"
    elif result.decision == "escalate_to_human":
        candidate.status = "pending_human"

    await session.flush()

    # Roster-critic liveness (Standing Phase 1b follow-up): the critic's
    # auto-trigger fires from vet_candidate completions, but when the LAST
    # vet finishes while judge reviews are still open, the area settles
    # HERE -- so this terminal path must re-check the trigger too, or the
    # critique never auto-fires for that area.
    from thinktank.handlers.vet_candidate import _maybe_enqueue_roster_critique

    await _maybe_enqueue_roster_critique(session, candidate.search_area)


async def promote_candidate_to_thinker(
    session: AsyncSession,
    candidate: CandidateThinker,
    result: CandidateReviewResponse,
) -> Thinker:
    """Create a Thinker from an approved CandidateThinker.

    Creates a new Thinker row, links the candidate to it, and
    sets the candidate status to "promoted".

    Args:
        session: Active database session.
        candidate: The approved candidate.
        result: The review response with tier, categories, etc.

    Returns:
        The newly created Thinker.
    """
    tier = result.tier if result.tier is not None else 3
    # ADMIN LO-02 (decisions path): same bug in the LLM promotion path as
    # in add_thinker/promote_candidate admin routes -- _slugify alone can
    # collide when two candidates share a normalized name.
    slug = await _unique_thinker_slug(session, candidate.name)

    thinker = Thinker(
        id=uuid.uuid4(),
        name=candidate.name,
        slug=slug,
        tier=tier,
        bio=f"Auto-promoted from candidate discovery. {candidate.name} was surfaced through cascade discovery.",
        approval_status="approved",
        active=True,
        added_at=_now(),
    )

    session.add(thinker)
    await session.flush()

    # Link candidate to the new thinker
    candidate.thinker_id = thinker.id
    candidate.status = "promoted"

    # Trigger discovery pipeline for the newly promoted thinker (cascade)
    discover_job = Job(
        id=uuid.uuid4(),
        job_type="discover_thinker",
        payload={"thinker_id": str(thinker.id)},
        priority=5,
        status="pending",
    )
    session.add(discover_job)

    # Trigger retroactive scan for promoted thinker
    rescan_job = Job(
        id=uuid.uuid4(),
        job_type="rescan_cataloged_for_thinker",
        payload={
            "thinker_id": str(thinker.id),
            "thinker_name": thinker.name,
        },
        priority=4,
        status="pending",
    )
    session.add(rescan_job)

    # Web-Lane Hardening W3.1: proactively discover the expert's OWNED
    # channels (their YouTube/podcast/Substack/site) so the corpus is
    # built from what they actually publish, not just where they guest.
    session.add(
        Job(
            id=uuid.uuid4(),
            job_type="discover_expert_sources",
            payload={"thinker_id": str(thinker.id)},
            priority=5,
            status="pending",
        )
    )
    session.add(
        Job(
            id=uuid.uuid4(),
            job_type="ingest_expert_papers",
            payload={"thinker_id": str(thinker.id)},
            priority=5,
            status="pending",
        )
    )

    # Expert pipeline (2026-07-12): candidates seeded by expert_search
    # carry VERIFIED platform hints in their evidence dossier. Register a
    # reachable YouTube channel as a pending source so ingestion starts as
    # soon as it clears source approval; a Substack hint is recorded on
    # the junction-free config (text ingestion is a v2 milestone).
    # ON CONFLICT-safe: sources.url is unique, so an existing row wins.
    evidence = candidate.evidence or {}
    youtube_block = evidence.get("youtube") or {}
    if youtube_block.get("checked") and youtube_block.get("reachable"):
        yt_url = youtube_block.get("url")
        existing_source = await session.execute(select(Source.id).where(Source.url == yt_url))
        if yt_url and existing_source.scalar_one_or_none() is None:
            source = Source(
                id=uuid.uuid4(),
                source_type="youtube_channel",
                name=f"{thinker.name} (YouTube)",
                url=yt_url,
                approval_status="pending_llm",
                host_name=thinker.name,
                config={"seeded_by": "expert_search", "search_area": candidate.search_area},
            )
            session.add(source)
            await session.flush()
            session.add(
                SourceThinker(
                    source_id=source.id,
                    thinker_id=thinker.id,
                    relationship_type="host",
                    added_at=_now(),
                )
            )
            session.add(
                Job(
                    id=uuid.uuid4(),
                    job_type="llm_approval_check",
                    payload={"review_type": "source_approval", "target_id": str(source.id)},
                    priority=5,
                    status="pending",
                )
            )

    return thinker
