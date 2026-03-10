"""Source management router for admin dashboard.

Provides filterable source list, manual source addition, approve/reject
with LLMReview audit trail, and force-refresh capability.
"""

import uuid
from datetime import datetime, UTC
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.thinktank.models.job import Job
from src.thinktank.models.review import LLMReview
from src.thinktank.models.source import Source
from src.thinktank.models.thinker import Thinker
from thinktank.admin.dependencies import get_session, get_templates

router = APIRouter(prefix="/admin/sources", tags=["sources"])
templates = get_templates()


async def _build_source_list(
    session: AsyncSession,
    thinker_id: Optional[str] = None,
    status: Optional[str] = None,
    source_type: Optional[str] = None,
) -> list[dict]:
    """Build source list with thinker names, applying filters."""
    stmt = select(Source, Thinker.name.label("thinker_name")).join(
        Thinker, Source.thinker_id == Thinker.id
    )

    if thinker_id and thinker_id != "all":
        try:
            tid = uuid.UUID(thinker_id)
            stmt = stmt.where(Source.thinker_id == tid)
        except ValueError:
            pass

    if status and status != "all":
        stmt = stmt.where(Source.approval_status == status)

    if source_type and source_type != "all":
        stmt = stmt.where(Source.source_type == source_type)

    stmt = stmt.order_by(Source.name)
    result = await session.execute(stmt)
    rows = result.all()

    sources = []
    for source, thinker_name in rows:
        sources.append({
            "id": str(source.id),
            "name": source.name,
            "url": source.url,
            "source_type": source.source_type,
            "approval_status": source.approval_status,
            "thinker_name": thinker_name,
            "thinker_id": str(source.thinker_id),
            "error_count": source.error_count,
            "last_fetched": source.last_fetched,
            "item_count": source.item_count,
            "active": source.active,
        })

    return sources


@router.get("/")
async def source_page(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Render the source management page with filter dropdowns populated."""
    result = await session.execute(select(Thinker).order_by(Thinker.name))
    thinkers = result.scalars().all()
    return templates.TemplateResponse(
        request,
        "sources.html",
        {"thinkers": thinkers},
    )


@router.get("/partials/list")
async def source_list_partial(
    request: Request,
    session: AsyncSession = Depends(get_session),
    thinker_id: Optional[str] = None,
    status: Optional[str] = None,
    source_type: Optional[str] = None,
):
    """HTML fragment: filterable source table."""
    sources = await _build_source_list(
        session, thinker_id=thinker_id, status=status, source_type=source_type
    )
    return templates.TemplateResponse(
        request,
        "partials/source_list.html",
        {"sources": sources},
    )


@router.get("/partials/add-form")
async def source_add_form_partial(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """HTML fragment: inline add source form with thinker dropdown."""
    result = await session.execute(select(Thinker).order_by(Thinker.name))
    thinkers = result.scalars().all()
    return templates.TemplateResponse(
        request,
        "partials/source_add_form.html",
        {"thinkers": thinkers},
    )


@router.post("/add")
async def add_source(
    request: Request,
    session: AsyncSession = Depends(get_session),
    name: str = Form(...),
    url: str = Form(...),
    thinker_id: str = Form(...),
    source_type: str = Form("podcast_rss"),
):
    """Create a new source with pending_llm status."""
    try:
        tid = uuid.UUID(thinker_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid thinker_id")

    source = Source(
        id=uuid.uuid4(),
        thinker_id=tid,
        name=name,
        url=url,
        source_type=source_type,
        approval_status="pending_llm",
        active=True,
    )
    session.add(source)
    await session.commit()

    sources = await _build_source_list(session)
    return templates.TemplateResponse(
        request,
        "partials/source_list.html",
        {"sources": sources, "success": f"Source '{name}' added with pending approval."},
    )


@router.post("/{source_id}/approve")
async def approve_source(
    request: Request,
    source_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    reason: str = Form(""),
):
    """Approve a pending source and create an LLMReview audit entry."""
    result = await session.execute(select(Source).where(Source.id == source_id))
    source = result.scalar_one_or_none()
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")

    source.approval_status = "approved"

    now = datetime.now(UTC).replace(tzinfo=None)
    review = LLMReview(
        id=uuid.uuid4(),
        review_type="source_approval",
        trigger="admin_override",
        decision="approve",
        decision_reasoning=reason,
        prompt_used="Admin manual approval",
        context_snapshot={"source_id": str(source_id), "source_name": source.name},
        created_at=now,
    )
    session.add(review)
    await session.commit()

    sources = await _build_source_list(session)
    return templates.TemplateResponse(
        request,
        "partials/source_list.html",
        {"sources": sources, "success": f"Source '{source.name}' approved."},
    )


@router.post("/{source_id}/reject")
async def reject_source(
    request: Request,
    source_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    reason: str = Form(""),
):
    """Reject a pending source and create an LLMReview audit entry."""
    result = await session.execute(select(Source).where(Source.id == source_id))
    source = result.scalar_one_or_none()
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")

    source.approval_status = "rejected"

    now = datetime.now(UTC).replace(tzinfo=None)
    review = LLMReview(
        id=uuid.uuid4(),
        review_type="source_approval",
        trigger="admin_override",
        decision="reject",
        decision_reasoning=reason,
        prompt_used="Admin manual rejection",
        context_snapshot={"source_id": str(source_id), "source_name": source.name},
        created_at=now,
    )
    session.add(review)
    await session.commit()

    sources = await _build_source_list(session)
    return templates.TemplateResponse(
        request,
        "partials/source_list.html",
        {"sources": sources, "success": f"Source '{source.name}' rejected."},
    )


@router.post("/{source_id}/force-refresh")
async def force_refresh_source(
    request: Request,
    source_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
):
    """Create a fetch_podcast_feed job for the source."""
    result = await session.execute(select(Source).where(Source.id == source_id))
    source = result.scalar_one_or_none()
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")

    job = Job(
        id=uuid.uuid4(),
        job_type="fetch_podcast_feed",
        payload={"source_id": str(source_id)},
        status="pending",
        priority=5,
    )
    session.add(job)
    await session.commit()

    sources = await _build_source_list(session)
    return templates.TemplateResponse(
        request,
        "partials/source_list.html",
        {"sources": sources, "success": f"Feed refresh queued for '{source.name}'."},
    )
