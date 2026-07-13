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
from thinktank.models.claim import Inquiry, InquiryPosition
from thinktank.models.job import Job
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
