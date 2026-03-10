"""Thinker management router for admin dashboard.

Provides full CRUD for thinkers: searchable/filterable list, inline add form
with LLM approval trigger, inline edit form, and active/inactive toggle.
"""

import re
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.thinktank.models.category import Category, ThinkerCategory
from src.thinktank.models.job import Job
from src.thinktank.models.source import Source
from src.thinktank.models.thinker import Thinker
from thinktank.admin.dependencies import get_session, get_templates

router = APIRouter(prefix="/admin/thinkers", tags=["thinkers"])
templates = get_templates()


def _slugify(name: str) -> str:
    """Generate a URL slug from a thinker name."""
    slug = name.lower().strip().replace(" ", "-")
    slug = re.sub(r"[^a-z0-9\-]", "", slug)
    return slug


async def _build_thinker_list(
    session: AsyncSession,
    q: Optional[str] = None,
    tier: Optional[int] = None,
    active: Optional[str] = None,
) -> list[dict]:
    """Build thinker list with source counts and category names, applying filters."""
    # Source count subquery
    source_count_sq = (
        select(
            Source.thinker_id,
            func.count(Source.id).label("source_count"),
        )
        .group_by(Source.thinker_id)
        .subquery()
    )

    # Main query
    stmt = select(Thinker, source_count_sq.c.source_count).outerjoin(
        source_count_sq, Thinker.id == source_count_sq.c.thinker_id
    )

    # Apply filters
    if q:
        stmt = stmt.where(Thinker.name.ilike(f"%{q}%"))
    if tier is not None:
        stmt = stmt.where(Thinker.tier == tier)
    if active and active != "all":
        if active == "true":
            stmt = stmt.where(Thinker.active.is_(True))
        elif active == "false":
            stmt = stmt.where(Thinker.active.is_(False))

    stmt = stmt.order_by(Thinker.name)
    result = await session.execute(stmt)
    rows = result.all()

    thinkers = []
    for thinker, source_count in rows:
        # Build category names from the selectin-loaded relationship
        category_names = []
        for tc in thinker.categories:
            cat_result = await session.execute(
                select(Category.name).where(Category.id == tc.category_id)
            )
            cat_name = cat_result.scalar_one_or_none()
            if cat_name:
                category_names.append(cat_name)

        thinkers.append({
            "id": str(thinker.id),
            "name": thinker.name,
            "slug": thinker.slug,
            "tier": thinker.tier,
            "active": thinker.active,
            "approval_status": thinker.approval_status,
            "source_count": source_count or 0,
            "category_names": ", ".join(category_names) if category_names else "",
        })

    return thinkers


@router.get("/")
async def thinker_page(request: Request):
    """Render the thinker management page with HTMX-loaded list."""
    return templates.TemplateResponse(request, "thinkers.html")


@router.get("/partials/list")
async def thinker_list_partial(
    request: Request,
    session: AsyncSession = Depends(get_session),
    q: Optional[str] = None,
    tier: Optional[int] = None,
    active: Optional[str] = None,
):
    """HTML fragment: thinker table with search/filter results."""
    thinkers = await _build_thinker_list(session, q=q, tier=tier, active=active)
    return templates.TemplateResponse(
        request,
        "partials/thinker_list.html",
        {"thinkers": thinkers},
    )


@router.get("/partials/add-form")
async def thinker_add_form_partial(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """HTML fragment: inline add thinker form with category multi-select."""
    result = await session.execute(select(Category).order_by(Category.name))
    categories = result.scalars().all()
    return templates.TemplateResponse(
        request,
        "partials/thinker_add_form.html",
        {"categories": categories},
    )


@router.post("/add")
async def add_thinker(
    request: Request,
    session: AsyncSession = Depends(get_session),
    name: str = Form(...),
    tier: int = Form(...),
    bio: str = Form(""),
):
    """Create a new thinker with LLM approval job."""
    # Parse category_ids from form (multi-select sends multiple values)
    form_data = await request.form()
    category_ids = form_data.getlist("category_ids")

    slug = _slugify(name)

    # Create thinker
    thinker = Thinker(
        id=uuid.uuid4(),
        name=name,
        slug=slug,
        tier=tier,
        bio=bio,
        approval_status="awaiting_llm",
        active=True,
    )
    session.add(thinker)
    await session.flush()

    # Create category associations
    for cid_str in category_ids:
        try:
            cid = uuid.UUID(cid_str)
        except ValueError:
            continue
        tc = ThinkerCategory(
            thinker_id=thinker.id,
            category_id=cid,
            relevance=5,
        )
        session.add(tc)

    # Create LLM approval job
    job = Job(
        id=uuid.uuid4(),
        job_type="llm_approval_check",
        payload={"entity_type": "thinker", "entity_id": str(thinker.id)},
        status="pending",
        priority=5,
    )
    session.add(job)

    await session.commit()

    # Re-render the thinker list with success message
    thinkers = await _build_thinker_list(session)
    return templates.TemplateResponse(
        request,
        "partials/thinker_list.html",
        {"thinkers": thinkers, "success": f"Thinker '{name}' added. LLM approval queued."},
    )


@router.get("/{thinker_id}/edit")
async def thinker_edit_form_partial(
    request: Request,
    thinker_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
):
    """HTML fragment: inline edit form for a specific thinker."""
    result = await session.execute(select(Thinker).where(Thinker.id == thinker_id))
    thinker = result.scalar_one_or_none()
    if thinker is None:
        return templates.TemplateResponse(
            request,
            "partials/thinker_list.html",
            {"thinkers": [], "error": "Thinker not found."},
        )

    # All categories for the multi-select
    cat_result = await session.execute(select(Category).order_by(Category.name))
    categories = cat_result.scalars().all()

    # Current category IDs for pre-selection
    current_category_ids = {str(tc.category_id) for tc in thinker.categories}

    return templates.TemplateResponse(
        request,
        "partials/thinker_edit_form.html",
        {
            "thinker": thinker,
            "categories": categories,
            "current_category_ids": current_category_ids,
        },
    )


@router.post("/{thinker_id}/edit")
async def edit_thinker(
    request: Request,
    thinker_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    name: str = Form(...),
    tier: int = Form(...),
    bio: str = Form(""),
    active: str = Form("off"),
):
    """Update a thinker's name, tier, bio, categories, and active status."""
    result = await session.execute(select(Thinker).where(Thinker.id == thinker_id))
    thinker = result.scalar_one_or_none()
    if thinker is None:
        thinkers = await _build_thinker_list(session)
        return templates.TemplateResponse(
            request,
            "partials/thinker_list.html",
            {"thinkers": thinkers, "error": "Thinker not found."},
        )

    # Parse category_ids from form
    form_data = await request.form()
    category_ids = form_data.getlist("category_ids")

    # Update fields
    thinker.name = name
    thinker.tier = tier
    thinker.bio = bio
    thinker.active = active == "on"

    # Replace categories: delete existing, insert new
    await session.execute(
        delete(ThinkerCategory).where(ThinkerCategory.thinker_id == thinker_id)
    )
    for cid_str in category_ids:
        try:
            cid = uuid.UUID(cid_str)
        except ValueError:
            continue
        tc = ThinkerCategory(
            thinker_id=thinker_id,
            category_id=cid,
            relevance=5,
        )
        session.add(tc)

    await session.commit()

    # Re-render the thinker list with success message
    thinkers = await _build_thinker_list(session)
    return templates.TemplateResponse(
        request,
        "partials/thinker_list.html",
        {"thinkers": thinkers, "success": f"Thinker '{name}' updated."},
    )


@router.post("/{thinker_id}/toggle-active")
async def toggle_thinker_active(
    request: Request,
    thinker_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
):
    """Toggle a thinker's active status without deleting any data."""
    result = await session.execute(select(Thinker).where(Thinker.id == thinker_id))
    thinker = result.scalar_one_or_none()
    if thinker is None:
        thinkers = await _build_thinker_list(session)
        return templates.TemplateResponse(
            request,
            "partials/thinker_list.html",
            {"thinkers": thinkers, "error": "Thinker not found."},
        )

    thinker.active = not thinker.active
    await session.commit()

    # Re-render the thinker list
    thinkers = await _build_thinker_list(session)
    status = "activated" if thinker.active else "deactivated"
    return templates.TemplateResponse(
        request,
        "partials/thinker_list.html",
        {"thinkers": thinkers, "success": f"Thinker '{thinker.name}' {status}."},
    )
