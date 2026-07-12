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

from thinktank.discovery.evidence import gather_evidence
from thinktank.discovery.rubric import gate_decision, load_thresholds, score_dossier
from thinktank.ingestion.name_matcher import match_thinkers_in_text
from thinktank.models.candidate import CandidateThinker
from thinktank.models.content import Content, ContentThinker
from thinktank.models.job import Job
from thinktank.queue.claim import _now
from thinktank.queue.retry import get_max_attempts

logger = structlog.get_logger(__name__)


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

    if candidate.status not in ("pending_llm", "vetting", "seeded"):
        log.info("vet_candidate_skipped", status=candidate.status)
        return

    # Hints: seed stage may have stored platform URLs in evidence.hints;
    # legacy candidates may carry suggested_youtube.
    hints = dict((candidate.evidence or {}).get("hints") or {})
    if candidate.suggested_youtube and "youtube_url" not in hints:
        hints["youtube_url"] = candidate.suggested_youtube

    dossier = await gather_evidence(session, candidate.name, hints=hints)
    peers = await _peer_coappearances(session, candidate.name)
    total, breakdown = score_dossier(dossier, peer_coappearances=peers)
    thresholds = await load_thresholds(session)
    outcome = gate_decision(total, breakdown, thresholds)

    candidate.evidence = dossier
    candidate.qualification_score = total
    candidate.score_breakdown = breakdown

    if outcome == "auto_rejected":
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
