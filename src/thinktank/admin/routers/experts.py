"""Expert Discovery admin page: launch area searches, browse results by area.

The operator-facing surface of the Expert Discovery & Vetting pipeline
(Amir spec 2026-07-12): type an area -> expert_search job; results appear
grouped by search_area with qualification scores, gate outcomes, evidence
chips, and an expandable dossier per candidate. Borderline candidates
(pending_human) are promoted/rejected here -- reusing the existing
candidate actions on the thinkers router.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from thinktank.admin.auth import require_admin
from thinktank.admin.dependencies import get_session, get_templates
from thinktank.models.candidate import CandidateThinker
from thinktank.models.job import Job

router = APIRouter(prefix="/admin/experts", tags=["experts"])
templates = get_templates()

# Display order for gate outcomes within an area section.
_STATUS_ORDER = {
    "promoted": 0,
    "awaiting_llm": 1,
    "pending_human": 2,
    "vetting": 3,
    "needs_more_data": 4,
    "rejected": 5,
    "rejected_duplicate": 6,
    "auto_rejected": 7,
}


@router.get("/")
async def experts_page(request: Request):
    """Render the Expert Discovery page; content loads via HTMX partials."""
    return templates.TemplateResponse(request, "experts.html", {})


@router.post("/search")
async def launch_search(
    request: Request,
    session: AsyncSession = Depends(get_session),
    area: str = Form(...),
    principal: str = Depends(require_admin),
):
    """Launch an expert_search job for an area (the Find Experts button)."""
    area = area.strip()
    if not area:
        raise HTTPException(status_code=422, detail="area is required")

    job = Job(
        id=uuid.uuid4(),
        job_type="expert_search",
        payload={"area": area, "triggered_by": principal},
        priority=5,
        status="pending",
        attempts=0,
        max_attempts=3,
    )
    session.add(job)
    await session.commit()

    return templates.TemplateResponse(
        request,
        "partials/trigger_result.html",
        {"success": True, "message": f"Expert search launched for '{area}'", "job_id": str(job.id)},
    )


@router.get("/partials/areas")
async def area_sections_partial(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """HTMX partial: per-area funnel summaries + candidate tables.

    One section per distinct search_area, newest activity first. The
    funnel line counts gate outcomes; candidates sort promoted-first
    then by score.
    """
    # Funnel counts per area
    counts_result = await session.execute(
        select(CandidateThinker.search_area, CandidateThinker.status, func.count())
        .where(CandidateThinker.search_area.is_not(None))
        .group_by(CandidateThinker.search_area, CandidateThinker.status)
    )
    funnel: dict[str, dict[str, int]] = {}
    for area, status, count in counts_result.all():
        funnel.setdefault(area, {})[status] = count

    # Candidates per area
    cands_result = await session.execute(
        select(CandidateThinker)
        .where(CandidateThinker.search_area.is_not(None))
        .order_by(CandidateThinker.first_seen_at.desc())
    )
    by_area: dict[str, list[CandidateThinker]] = {}
    for cand in cands_result.scalars().all():
        by_area.setdefault(cand.search_area, []).append(cand)

    areas = []
    for area, candidates in by_area.items():
        candidates.sort(key=lambda c: (_STATUS_ORDER.get(c.status, 9), -(c.qualification_score or 0)))
        statuses = funnel.get(area, {})
        areas.append(
            {
                "name": area,
                "candidates": candidates,
                "surfaced": sum(statuses.values()),
                "promoted": statuses.get("promoted", 0),
                "in_review": statuses.get("awaiting_llm", 0) + statuses.get("pending_human", 0),
                "vetting": statuses.get("vetting", 0),
                "rejected": statuses.get("auto_rejected", 0)
                + statuses.get("rejected", 0)
                + statuses.get("rejected_duplicate", 0),
            }
        )
    # Newest activity first: max first_seen among the area's candidates.
    areas.sort(key=lambda a: max(c.first_seen_at for c in a["candidates"]), reverse=True)

    return templates.TemplateResponse(request, "partials/expert_areas.html", {"areas": areas})


@router.get("/candidates/{candidate_id}/dossier")
async def candidate_dossier_partial(
    request: Request,
    candidate_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
):
    """HTMX partial: full evidence dossier + score breakdown for one candidate."""
    candidate = await session.get(CandidateThinker, candidate_id)
    if candidate is None:
        raise HTTPException(status_code=404, detail="Candidate not found")

    evidence = candidate.evidence or {}
    return templates.TemplateResponse(
        request,
        "partials/expert_dossier.html",
        {
            "c": candidate,
            "evidence": evidence,
            "breakdown": candidate.score_breakdown or {},
            "seed_claim": evidence.get("seed_claim") or {},
            "openalex": evidence.get("openalex") or {},
            "wikidata": evidence.get("wikidata") or {},
            "books": evidence.get("openlibrary") or {},
            "podcasts": evidence.get("podcastindex") or {},
            "youtube": evidence.get("youtube") or {},
            "substack": evidence.get("substack") or {},
        },
    )
