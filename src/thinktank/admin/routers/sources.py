"""Source management router for admin dashboard.

Provides filterable source list, manual source addition, approve/reject
with LLMReview audit trail, force-refresh capability, and source detail
page with health summary, episodes list, and error history.
"""

import re
import uuid
from datetime import datetime, UTC
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from thinktank.models.content import Content
from thinktank.models.job import Job
from thinktank.models.review import LLMReview
from thinktank.models.source import Source, SourceThinker
from thinktank.models.thinker import Thinker
from thinktank.admin.dependencies import get_session, get_templates

router = APIRouter(prefix="/admin/sources", tags=["sources"])
templates = get_templates()


async def _build_source_list(
    session: AsyncSession,
    thinker_id: Optional[str] = None,
    status: Optional[str] = None,
    source_type: Optional[str] = None,
) -> list[dict]:
    """Build source list with associated thinker names via junction, applying filters."""
    # Eager-load source_thinkers.thinker so thinker names are available on each
    # Source row without triggering a per-row SELECT.
    stmt = select(Source).options(
        selectinload(Source.source_thinkers).selectinload(SourceThinker.thinker)
    )

    if thinker_id and thinker_id != "all":
        try:
            tid = uuid.UUID(thinker_id)
            stmt = stmt.join(SourceThinker).where(SourceThinker.thinker_id == tid)
        except ValueError:
            pass

    if status and status != "all":
        stmt = stmt.where(Source.approval_status == status)

    if source_type and source_type != "all":
        stmt = stmt.where(Source.source_type == source_type)

    stmt = stmt.order_by(Source.name)
    result = await session.execute(stmt)
    source_rows = result.scalars().all()

    sources = []
    for source in source_rows:
        # Thinker names are eager-loaded via source_thinkers -> thinker.
        thinker_names = [
            st.thinker.name for st in source.source_thinkers if st.thinker is not None
        ]

        sources.append({
            "id": str(source.id),
            "name": source.name,
            "url": source.url,
            "source_type": source.source_type,
            "approval_status": source.approval_status,
            "thinker_names": ", ".join(thinker_names) if thinker_names else "—",
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
    thinker_id: str = Form(""),
    source_type: str = Form("podcast_rss"),
):
    """Create a new source with pending_llm status. Thinker is optional."""
    source = Source(
        id=uuid.uuid4(),
        thinker_id=None,
        name=name,
        url=url,
        source_type=source_type,
        approval_status="pending_llm",
        active=True,
    )

    # Extract YouTube channel ID from URL for youtube_channel sources
    if source_type == "youtube_channel":
        match = re.search(r"youtube\.com/channel/(UC[a-zA-Z0-9_-]+)", url)
        if match:
            source.external_id = match.group(1)

    session.add(source)

    # Create junction row if thinker provided
    if thinker_id:
        try:
            tid = uuid.UUID(thinker_id)
            junction = SourceThinker(
                source_id=source.id,
                thinker_id=tid,
                relationship_type="curated",
            )
            session.add(junction)
        except ValueError:
            pass

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
    """Create a fetch job for the source based on its type."""
    result = await session.execute(select(Source).where(Source.id == source_id))
    source = result.scalar_one_or_none()
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")

    # Dispatch job type based on source_type
    if source.source_type == "youtube_channel":
        job_type = "fetch_youtube_channel"
    else:
        job_type = "fetch_podcast_feed"

    job = Job(
        id=uuid.uuid4(),
        job_type=job_type,
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


@router.get("/{source_id}/partials/episodes")
async def source_episodes_partial(
    request: Request,
    source_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
):
    """HTML fragment: episodes (content) table for a source, lazy-loaded via HTMX."""
    stmt = (
        select(Content)
        .where(Content.source_id == source_id)
        .order_by(Content.published_at.desc().nulls_last())
        .limit(50)
    )
    result = await session.execute(stmt)
    episodes = result.scalars().all()
    return templates.TemplateResponse(
        request,
        "partials/source_episodes.html",
        {"episodes": episodes},
    )


@router.get("/{source_id}/partials/errors")
async def source_errors_partial(
    request: Request,
    source_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
):
    """HTML fragment: error history from failed fetch_podcast_feed jobs for this source."""
    stmt = text(
        "SELECT id, error, error_category, created_at, completed_at "
        "FROM jobs "
        "WHERE job_type = 'fetch_podcast_feed' "
        "AND status = 'failed' "
        "AND payload->>'source_id' = :sid "
        "ORDER BY created_at DESC LIMIT 20"
    )
    result = await session.execute(stmt, {"sid": str(source_id)})
    errors = result.mappings().all()
    return templates.TemplateResponse(
        request,
        "partials/source_errors.html",
        {"errors": errors},
    )


@router.get("/{source_id}")
async def source_detail(
    request: Request,
    source_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
):
    """Source detail page with health summary and HTMX-loaded sections."""
    result = await session.execute(select(Source).where(Source.id == source_id))
    source = result.scalar_one_or_none()
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")

    # Get associated thinkers via junction
    thinker_result = await session.execute(
        select(Thinker.name, SourceThinker.relationship_type)
        .join(SourceThinker, SourceThinker.thinker_id == Thinker.id)
        .where(SourceThinker.source_id == source_id)
    )
    associated_thinkers = [
        {"name": r[0], "relationship_type": r[1]} for r in thinker_result.all()
    ]
    thinker_name = associated_thinkers[0]["name"] if associated_thinkers else "—"

    return templates.TemplateResponse(
        request,
        "source_detail.html",
        {
            "source": source,
            "thinker_name": thinker_name,
            "associated_thinkers": associated_thinkers,
        },
    )
