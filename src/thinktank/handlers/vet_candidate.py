"""Handler: vet_candidate -- evidence gathering + deterministic gate.

Expert Discovery & Vetting pipeline, Stage 2 (Amir spec 2026-07-12).
Zero LLM tokens: gathers the structured evidence dossier, scores it with
the rubric, and routes by gate outcome:

    auto_rejected -> status set, terminal (never costs an LLM call)
    borderline    -> status pending_human (admin candidate queue decides)
    shortlisted   -> status awaiting_llm + llm_approval_check enqueued
                     (the existing candidate_review flow judges the
                     dossier and promotes on approval)

Peer signal: co-appearance count = episodes already attributed to
tracked thinkers whose title mentions the candidate (ILIKE prefilter +
word-boundary confirmation, same guard as the rescan handler).
"""

from __future__ import annotations

import uuid

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from thinktank.discovery.adjudicator import review_rejection
from thinktank.discovery.evidence import gather_evidence
from thinktank.discovery.rubric import gate_decision, load_thresholds, score_dossier
from thinktank.ingestion.name_matcher import match_thinkers_in_text
from thinktank.models.candidate import CandidateThinker
from thinktank.models.content import Content, ContentThinker
from thinktank.models.job import Job
from thinktank.queue.claim import _now
from thinktank.queue.retry import get_max_attempts

logger = structlog.get_logger(__name__)


def _rejection_is_suspicious(seed_claim: dict, dossier: dict) -> bool:
    """Trigger the rejection-review adjudicator only on the failure pattern.

    Fires when the candidate was surfaced with a real eminence claim yet
    BOTH scholarship and notability came back not-found -- implausible for
    a claimed authority, and the signature of an evidence-lookup failure.
    Keeps the LLM call off the common path (weak candidates with weak
    evidence are rejected deterministically, no tokens).
    """
    if not (seed_claim or {}).get("basis"):
        return False
    oa = dossier.get("openalex", {})
    wd = dossier.get("wikidata", {})
    return not oa.get("found") and not wd.get("found")


async def _peer_coappearances(session: AsyncSession, name: str) -> int:
    """Episodes attributed to tracked thinkers that mention the candidate."""
    stmt = (
        select(Content.id, Content.title, Content.description)
        .join(ContentThinker, ContentThinker.content_id == Content.id)
        .where(Content.title.ilike(f"%{name}%"))
        .distinct()
        .limit(50)
    )
    result = await session.execute(stmt)
    fake_thinker = [{"id": uuid.uuid4(), "name": name}]
    count = 0
    for _cid, title, description in result.all():
        if match_thinkers_in_text(title or "", description or "", fake_thinker, None):
            count += 1
    return count


async def handle_vet_candidate(session: AsyncSession, job: Job) -> None:
    """Vet one candidate: dossier -> score -> gate -> route.

    Job payload schema: {"candidate_id": "uuid-str"}
    """
    candidate_id_str = job.payload.get("candidate_id")
    if not candidate_id_str:
        raise ValueError("candidate_id missing from job payload")

    candidate = await session.get(CandidateThinker, uuid.UUID(candidate_id_str))
    if candidate is None:
        raise ValueError(f"Candidate {candidate_id_str} not found")

    log = logger.bind(job_id=str(job.id), candidate=candidate.name)

    # ``force`` re-vets a candidate the gate already decided -- used to
    # reprocess auto_rejected/pending_human rows after an evidence-parsing
    # fix. Never re-vet an already-promoted candidate (it's a live thinker).
    force = bool(job.payload.get("force"))
    allowed = ("pending_llm", "vetting", "seeded")
    if candidate.status == "promoted" or (candidate.status not in allowed and not force):
        log.info("vet_candidate_skipped", status=candidate.status, force=force)
        return

    # Hints + adjudication context: seed stage stored platform URLs and a
    # seed_claim (basis/affiliation) in evidence; both feed the LLM
    # adjudicator when a structured source is ambiguous.
    prior_evidence = candidate.evidence or {}
    hints = dict(prior_evidence.get("hints") or {})
    if candidate.suggested_youtube and "youtube_url" not in hints:
        hints["youtube_url"] = candidate.suggested_youtube
    seed_claim = prior_evidence.get("seed_claim") or {}
    adjudicate_ctx = {
        "name": candidate.name,
        "search_area": candidate.search_area,
        "seed_basis": seed_claim.get("basis"),
        "affiliation": seed_claim.get("affiliation"),
    }

    dossier = await gather_evidence(session, candidate.name, hints=hints, adjudicate_ctx=adjudicate_ctx)
    dossier["seed_claim"] = seed_claim  # preserve across re-vets
    peers = await _peer_coappearances(session, candidate.name)
    total, breakdown = score_dossier(dossier, peer_coappearances=peers)
    thresholds = await load_thresholds(session)
    outcome = gate_decision(total, breakdown, thresholds)

    candidate.evidence = dossier
    candidate.qualification_score = total
    candidate.score_breakdown = breakdown

    if outcome == "auto_rejected":
        # On-ambiguity self-check: a candidate the seed surfaced as a
        # recognized expert but whose structured evidence came back empty
        # is more likely a lookup failure than a genuine non-expert
        # (Juan Carlos Izpisúa Belmonte: sch=0/not=0). Ask the adjudicator
        # before silently rejecting; not-legitimate routes to a human.
        if _rejection_is_suspicious(seed_claim, dossier):
            legitimate, meta = await review_rejection(
                session, candidate.name, candidate.search_area or "", seed_claim.get("basis"), dossier, total
            )
            dossier["rejection_review"] = meta
            candidate.evidence = dossier
            if not legitimate:
                candidate.status = "pending_human"
                await session.commit()
                log.info("vet_candidate_complete", outcome="rejection_overturned", score=total)
                return
        candidate.status = "auto_rejected"
        candidate.reviewed_by = "vetting_gate"
        candidate.reviewed_at = _now()
    elif outcome == "borderline":
        candidate.status = "pending_human"
    else:  # shortlisted
        candidate.status = "awaiting_llm"
        session.add(
            Job(
                id=uuid.uuid4(),
                job_type="llm_approval_check",
                payload={
                    "review_type": "candidate_review",
                    # target_id drives apply_candidate_decision;
                    # candidate_ids scopes the context snapshot.
                    "target_id": str(candidate.id),
                    "candidate_ids": [str(candidate.id)],
                },
                priority=5,
                status="pending",
                attempts=0,
                max_attempts=get_max_attempts("llm_approval_check"),
                created_at=_now(),
            )
        )

    await session.commit()

    log.info(
        "vet_candidate_complete",
        outcome=outcome,
        score=total,
        breakdown=breakdown,
        peer_coappearances=peers,
    )
