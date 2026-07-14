"""Handler: run_inquiry -- pose a question across an area's expert roster.

Claims v2 PR 3, Mode A (proactive). For one inquiry:

1. Propositionize the question into a headline canonical claim.
2. Resolve the roster: promoted candidates for the area (search_area)
   plus approved thinkers categorized under it.
3. Per expert, two evidence lanes:
      corpus -- embed the question, ANN over that expert's
                content_chunks, LLM-extract claims per content item,
                HARD grounding (quote located in body_text, offsets
                stored).
      web    -- one Exa search (clean text + publish dates inline),
                store cited pages as Documents, LLM-extract from page
                text, grounded when the quote locates in the stored text.
4. Every observation is embedded and resolved onto a canonical claim
   (attach-or-create; fine-grained claims parent to the headline).
5. Per expert, a REQUIRED inquiry_positions row -- the stance-matrix
   cell -- synthesized from their observations ('unknown' when nothing
   addressed the question).

Runs ONLY on the Mac worker (WORKER_JOB_TYPES routing): both lanes need
the /embed endpoint on the local inference service.

Commit discipline: one commit per expert (a mid-roster crash retries
without losing completed experts; positions are idempotently upserted).

Job payload schema: {"inquiry_id": "uuid-str"}
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import structlog
from sqlalchemy import delete, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from thinktank.discovery.claim_resolution import resolve_claim
from thinktank.discovery.exa_client import exa_search
from thinktank.embeddings import embed_texts
from thinktank.ingestion.web_fetch import fetch_document, store_exa_result
from thinktank.llm.claims_extraction import (
    ExtractedClaim,
    extract_observations,
    ground_quote,
    propositionize,
    resolve_position,
)
from thinktank.models.candidate import CandidateThinker
from thinktank.models.claim import Claim, ClaimObservation, ContentChunk, Inquiry, InquiryPosition
from thinktank.models.content import Content, ContentThinker
from thinktank.models.job import Job
from thinktank.models.thinker import Thinker
from thinktank.queue.leader import session_advisory_lock

logger = structlog.get_logger(__name__)

# Corpus lane: top-K chunks per expert by cosine similarity to the question.
CORPUS_TOP_K = 8
# Web lane: citations fetched per expert (each is an HTTP GET + extraction).
WEB_CITATIONS_PER_EXPERT = 3


async def _resolve_roster(session: AsyncSession, area: str | None) -> list[Thinker]:
    """Vetted experts for an area: promoted candidates + categorized thinkers.

    With no area, the roster is every active approved thinker (small
    corpus for now; revisit if the roster grows past LLM-cost comfort).
    """
    base = select(Thinker).where(Thinker.active.is_(True), Thinker.approval_status == "approved")
    if area is None:
        return list((await session.execute(base)).scalars().all())

    pattern = f"%{area}%"
    promoted_ids = select(CandidateThinker.thinker_id).where(
        CandidateThinker.status == "promoted",
        CandidateThinker.thinker_id.is_not(None),
        CandidateThinker.search_area.ilike(pattern),
    )
    from thinktank.models.category import Category, ThinkerCategory

    categorized_ids = (
        select(ThinkerCategory.thinker_id)
        .join(Category, Category.id == ThinkerCategory.category_id)
        .where(Category.name.ilike(pattern))
    )
    result = await session.execute(base.where(or_(Thinker.id.in_(promoted_ids), Thinker.id.in_(categorized_ids))))
    return list(result.scalars().all())


async def _corpus_evidence(
    session: AsyncSession, thinker_id: uuid.UUID, question_embedding: list[float]
) -> list[tuple[Content, str]]:
    """Top-K question-relevant chunks for one expert, grouped per content.

    Returns (content, joined_chunk_text) bundles -- one LLM extraction
    call per content item, so quotes ground against a single body_text.
    """
    distance = ContentChunk.embedding.cosine_distance(question_embedding)
    rows = (
        await session.execute(
            select(ContentChunk, Content)
            .join(Content, Content.id == ContentChunk.content_id)
            .join(ContentThinker, ContentThinker.content_id == Content.id)
            .where(ContentThinker.thinker_id == thinker_id, ContentChunk.embedding.is_not(None))
            .order_by(distance)
            .limit(CORPUS_TOP_K)
        )
    ).all()

    by_content: dict[uuid.UUID, tuple[Content, list[ContentChunk]]] = {}
    for chunk, content in rows:
        by_content.setdefault(content.id, (content, []))[1].append(chunk)
    return [
        (content, "\n\n".join(c.text for c in sorted(chunks, key=lambda c: c.chunk_index)))
        for content, chunks in by_content.values()
    ]


async def _store_observations(
    session: AsyncSession,
    claims: list[ExtractedClaim],
    *,
    inquiry: Inquiry,
    thinker: Thinker,
    grounding_text: str | None,
    content: Content | None = None,
    document=None,
    asserted_at: datetime | None,
    extraction_model: str,
) -> list[ClaimObservation]:
    """Embed, canonically resolve, and persist one bundle's observations."""
    if not claims:
        return []
    vectors = await embed_texts([c.claim_text for c in claims])
    stored: list[ClaimObservation] = []
    for extracted, vector in zip(claims, vectors, strict=True):
        offsets = ground_quote(extracted.quote, grounding_text) if grounding_text else None
        canonical = await resolve_claim(
            session,
            claim_text=extracted.claim_text,
            claim_type=extracted.claim_type,
            embedding=vector,
            parent_claim_id=inquiry.canonical_claim_id,
            asserted_at=asserted_at,
        )
        await session.flush()  # canonical.id needed for the FK below
        stored.append(
            ClaimObservation(
                id=uuid.uuid4(),
                claim_id=canonical.id,
                inquiry_id=inquiry.id,
                thinker_id=thinker.id,
                origin="inquiry",
                claim_type=extracted.claim_type,
                stance=extracted.stance_on_question,
                claim_text=extracted.claim_text,
                confidence=extracted.confidence,
                quote=extracted.quote,
                quote_start=offsets[0] if offsets else None,
                quote_end=offsets[1] if offsets else None,
                grounded=offsets is not None,
                content_id=content.id if content is not None else None,
                document_id=document.id if document is not None else None,
                asserted_at=asserted_at,
                topics=extracted.topics or None,
                embedding=vector,
                extraction_model=extraction_model,
            )
        )
    session.add_all(stored)
    return stored


async def handle_run_inquiry(session: AsyncSession, job: Job) -> None:
    """Run one proactive inquiry end-to-end.

    Serialized per inquiry by a session-scoped advisory lock: a stale-job
    reclaim (or an accidental duplicate enqueue) can produce two
    run_inquiry jobs for the same inquiry running concurrently on two
    worker slots. Without the lock they race -- both pass the per-expert
    idempotency check, both delete each other's in-flight observations,
    and one expert ends up with doubled observations and a coin-flip
    stance (observed live on the rapamycin run, 2026-07-13). The lock
    makes the second job skip cleanly.
    """
    inquiry_id_str = job.payload.get("inquiry_id")
    if not inquiry_id_str:
        raise ValueError("inquiry_id missing from run_inquiry payload")
    inquiry = await session.get(Inquiry, uuid.UUID(inquiry_id_str))
    if inquiry is None:
        raise ValueError(f"Inquiry {inquiry_id_str} not found")

    log = logger.bind(job_id=str(job.id), inquiry_id=inquiry_id_str, question=inquiry.question[:60])

    async with session_advisory_lock(session.bind, f"run_inquiry:{inquiry_id_str}") as acquired:
        if not acquired:
            log.info("inquiry_skipped_concurrent", reason="another job holds the inquiry lock")
            return
        await _run_inquiry_locked(session, job, inquiry, log)


async def _run_inquiry_locked(session: AsyncSession, job: Job, inquiry: Inquiry, log) -> None:
    from thinktank.config import get_settings

    extraction_model = get_settings().llm_model

    inquiry.status = "running"
    await session.commit()

    # 1. Headline canonical claim (idempotent on retry).
    if inquiry.canonical_claim_id is None:
        proposition = await propositionize(session, inquiry.question)
        [headline_embedding] = await embed_texts([proposition.proposition])
        headline = Claim(
            id=uuid.uuid4(),
            proposition=proposition.proposition,
            claim_type=proposition.claim_type,
            embedding=headline_embedding,
        )
        session.add(headline)
        inquiry.canonical_claim_id = headline.id
        await session.commit()

    # 2. Roster.
    roster = await _resolve_roster(session, inquiry.area)
    if not roster:
        log.warning("inquiry_empty_roster", area=inquiry.area)
        inquiry.status = "complete"
        inquiry.completed_at = datetime.now(UTC)
        await session.commit()
        return
    log.info("inquiry_roster_resolved", experts=len(roster))

    [question_embedding] = await embed_texts([inquiry.question])

    # 3. Per expert: two lanes -> observations -> required position.
    # Iterate by PK and re-fetch each expert fresh: a per-expert rollback
    # (below) expires EVERY ORM object, including not-yet-processed roster
    # entries -- holding them across the loop would trip an implicit sync
    # refresh (-> MissingGreenlet) on the next iteration.
    inquiry_pk = inquiry.id
    roster_ids = [t.id for t in roster]
    failed_experts = 0
    for thinker_id in roster_ids:
        # Retry idempotency: an expert with a position row is complete.
        done = await session.get(InquiryPosition, (inquiry_pk, thinker_id))
        if done is not None:
            continue
        thinker = await session.get(Thinker, thinker_id)
        slug = thinker.slug
        try:
            await _resolve_expert(session, inquiry, thinker, question_embedding, extraction_model, log)
        except Exception:
            # Per-expert resilience: one expert's transient failure (a slow
            # web fetch, an LLM blip) must not sink the whole inquiry or
            # block every expert after it. Roll back this expert's partial
            # work, log, and continue; a re-run retries them (no position
            # row was written).
            await session.rollback()
            # rollback() expires all ORM objects (regardless of
            # expire_on_commit); re-attach the inquiry so the next
            # iteration and the final status update don't trigger an
            # implicit sync refresh (-> MissingGreenlet under asyncio).
            inquiry = await session.get(Inquiry, inquiry_pk)
            failed_experts += 1
            log.warning("inquiry_expert_failed", thinker=slug, exc_info=True)

    inquiry.status = "complete"
    inquiry.completed_at = datetime.now(UTC)
    await session.commit()
    log.info("inquiry_complete", experts=len(roster), failed_experts=failed_experts)


async def _resolve_expert(
    session: AsyncSession,
    inquiry: Inquiry,
    thinker: Thinker,
    question_embedding: list[float],
    extraction_model: str,
    log,
) -> None:
    """Resolve one expert's stance-matrix cell (both lanes + position).

    One transaction, ending in the per-expert commit -- so a failure
    rolls back cleanly and leaves no position row (re-run retries).
    """
    # A crash mid-expert leaves orphan observations; rebuild cleanly.
    await session.execute(
        delete(ClaimObservation).where(
            ClaimObservation.inquiry_id == inquiry.id, ClaimObservation.thinker_id == thinker.id
        )
    )

    observations: list[ClaimObservation] = []
    dropped_total = 0

    # Corpus lane (hard grounding against body_text).
    for content, evidence in await _corpus_evidence(session, thinker.id, question_embedding):
        claims, dropped = await extract_observations(
            session, inquiry.question, thinker.name, evidence, evidence_kind="podcast transcript"
        )
        dropped_total += dropped
        observations += await _store_observations(
            session,
            claims,
            inquiry=inquiry,
            thinker=thinker,
            grounding_text=content.body_text,
            content=content,
            asserted_at=content.published_at,
            extraction_model=extraction_model,
        )

    # Web lane (per-expert Exa search: clean text + publish dates
    # inline, so no re-fetch; grounding against the stored page text).
    query = f"What has {thinker.name} said about: {inquiry.question}"
    results = await exa_search(session, query, num_results=WEB_CITATIONS_PER_EXPERT)
    for result in results:
        document = await store_exa_result(session, result, found_via="inquiry_web_lane", search_query=query)
        if document is None or not document.text_content:
            # Exa returned a hit without usable text -- try the fetch fallback chain.
            document = await fetch_document(session, result.url, found_via="inquiry_web_lane", search_query=query)
        if document is None or not document.text_content:
            continue
        claims, dropped = await extract_observations(
            session, inquiry.question, thinker.name, document.text_content, evidence_kind="web article"
        )
        dropped_total += dropped
        observations += await _store_observations(
            session,
            claims,
            inquiry=inquiry,
            thinker=thinker,
            grounding_text=document.text_content,
            document=document,
            asserted_at=document.published_at,
            extraction_model=extraction_model,
        )

    # 4. REQUIRED stance-matrix cell.
    position = await resolve_position(
        session,
        inquiry.question,
        thinker.name,
        [{"stance": o.stance, "confidence": o.confidence, "claim_text": o.claim_text} for o in observations],
    )
    await session.execute(
        pg_insert(InquiryPosition)
        .values(
            inquiry_id=inquiry.id,
            thinker_id=thinker.id,
            stance=position.stance,
            position_summary=position.summary,
            observation_count=len(observations),
            resolution_model=extraction_model,
        )
        .on_conflict_do_update(
            index_elements=["inquiry_id", "thinker_id"],
            set_={
                "stance": position.stance,
                "position_summary": position.summary,
                "observation_count": len(observations),
                "resolution_model": extraction_model,
                "resolved_at": datetime.now(UTC),
            },
        )
    )
    await session.commit()
    log.info(
        "inquiry_expert_resolved",
        thinker=thinker.slug,
        stance=position.stance,
        observations=len(observations),
        ungrounded_dropped=dropped_total,
    )
