"""Inquiries admin page: launch proactive inquiries, watch them resolve.

Claims v2 PR 3 -- the minimal operator surface: type a question (and
optionally an area) -> Inquiry row + run_inquiry job; the list below
shows each inquiry's status and how many expert positions have resolved.
The full stance matrix (experts x proposition with receipts) is the PR 4
surface.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from thinktank.admin.auth import require_admin
from thinktank.admin.dependencies import get_session, get_templates
from thinktank.models.claim import Claim, ClaimObservation, Document, Inquiry, InquiryPosition
from thinktank.models.content import Content
from thinktank.models.job import Job
from thinktank.models.thinker import Thinker
from thinktank.queue.retry import get_max_attempts

router = APIRouter(prefix="/admin/inquiries", tags=["inquiries"])
templates = get_templates()


@router.get("/")
async def inquiries_page(request: Request):
    """Render the Inquiries page; the list loads via an HTMX partial."""
    return templates.TemplateResponse(request, "inquiries.html", {})


@router.post("/launch")
async def launch_inquiry(
    request: Request,
    session: AsyncSession = Depends(get_session),
    question: str = Form(...),
    area: str = Form(""),
    principal: str = Depends(require_admin),
):
    """Create an Inquiry and enqueue its run_inquiry job."""
    question = question.strip()
    if not question:
        raise HTTPException(status_code=422, detail="question is required")

    inquiry = Inquiry(
        id=uuid.uuid4(),
        question=question,
        area=area.strip() or None,
        triggered_by=principal,
    )
    session.add(inquiry)
    session.add(
        Job(
            id=uuid.uuid4(),
            job_type="run_inquiry",
            payload={"inquiry_id": str(inquiry.id), "triggered_by": principal},
            priority=5,
            status="pending",
            attempts=0,
            max_attempts=get_max_attempts("run_inquiry"),
        )
    )
    await session.commit()

    return templates.TemplateResponse(
        request,
        "partials/trigger_result.html",
        {"success": True, "message": f"Inquiry launched: '{question[:80]}'", "job_id": str(inquiry.id)},
    )


@router.get("/partials/list")
async def inquiries_list_partial(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Inquiry rows with resolved-position counts, newest first."""
    positions = (
        select(
            InquiryPosition.inquiry_id,
            func.count().label("total"),
            func.count().filter(InquiryPosition.stance != "unknown").label("with_stance"),
        )
        .group_by(InquiryPosition.inquiry_id)
        .subquery()
    )
    rows = (
        await session.execute(
            select(Inquiry, positions.c.total, positions.c.with_stance)
            .outerjoin(positions, positions.c.inquiry_id == Inquiry.id)
            .order_by(Inquiry.created_at.desc())
            .limit(50)
        )
    ).all()
    inquiries = [
        {"inquiry": inquiry, "positions": total or 0, "with_stance": with_stance or 0}
        for inquiry, total, with_stance in rows
    ]
    return templates.TemplateResponse(request, "partials/inquiry_list.html", {"inquiries": inquiries})


# Display order: firm positions first, unknown last.
_STANCE_ORDER = {"asserts": 0, "denies": 1, "hedges": 2, "questions": 3, "reports": 4, "unknown": 5}


@router.get("/{inquiry_id}")
async def inquiry_detail_page(
    request: Request,
    inquiry_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
):
    """Render one inquiry's page; the stance matrix loads via a partial."""
    inquiry = await session.get(Inquiry, inquiry_id)
    if inquiry is None:
        raise HTTPException(status_code=404, detail="Inquiry not found")
    headline = await session.get(Claim, inquiry.canonical_claim_id) if inquiry.canonical_claim_id else None
    return templates.TemplateResponse(request, "inquiry_detail.html", {"inquiry": inquiry, "headline": headline})


@router.get("/{inquiry_id}/partials/matrix")
async def stance_matrix_partial(
    request: Request,
    inquiry_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
):
    """HTMX partial: experts x stance matrix with position summaries."""
    inquiry = await session.get(Inquiry, inquiry_id)
    if inquiry is None:
        raise HTTPException(status_code=404, detail="Inquiry not found")

    rows = (
        await session.execute(
            select(InquiryPosition, Thinker)
            .join(Thinker, Thinker.id == InquiryPosition.thinker_id)
            .where(InquiryPosition.inquiry_id == inquiry_id)
        )
    ).all()
    positions = sorted(
        ({"position": pos, "thinker": thinker} for pos, thinker in rows),
        key=lambda r: (_STANCE_ORDER.get(r["position"].stance, 9), r["thinker"].name),
    )
    distribution = {stance: 0 for stance in _STANCE_ORDER}
    for row in positions:
        distribution[row["position"].stance] = distribution.get(row["position"].stance, 0) + 1
    return templates.TemplateResponse(
        request,
        "partials/stance_matrix.html",
        {
            "inquiry": inquiry,
            "positions": positions,
            "distribution": {k: v for k, v in distribution.items() if v},
        },
    )


@router.get("/{inquiry_id}/experts/{thinker_id}/observations")
async def observation_receipts_partial(
    request: Request,
    inquiry_id: uuid.UUID,
    thinker_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
):
    """HTMX partial: one expert's evidence receipts for an inquiry.

    Every observation renders with its verbatim quote, provenance badge
    (episode title or linked web page), asserted_at date, and grounding
    flag -- the receipts behind the stance-matrix cell.
    """
    rows = (
        await session.execute(
            select(ClaimObservation, Content, Document)
            .outerjoin(Content, Content.id == ClaimObservation.content_id)
            .outerjoin(Document, Document.id == ClaimObservation.document_id)
            .where(
                ClaimObservation.inquiry_id == inquiry_id,
                ClaimObservation.thinker_id == thinker_id,
            )
            .order_by(ClaimObservation.asserted_at.desc().nulls_last())
        )
    ).all()
    observations = [{"obs": obs, "content": content, "document": document} for obs, content, document in rows]
    return templates.TemplateResponse(request, "partials/observation_receipts.html", {"observations": observations})
