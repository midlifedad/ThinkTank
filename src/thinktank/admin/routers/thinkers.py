"""Thinker management router for admin dashboard.

Provides full CRUD for thinkers: searchable/filterable list, inline add form
with LLM approval trigger, inline edit form, active/inactive toggle,
thinker detail page with HTMX-loaded sections, candidate queue with
promote/reject, and PodcastIndex discovery trigger.
"""

import re
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from thinktank.admin.auth import require_admin
from thinktank.admin.dependencies import get_session, get_templates
from thinktank.models.candidate import CandidateThinker
from thinktank.models.category import Category, ThinkerCategory
from thinktank.models.content import Content, ContentThinker
from thinktank.models.job import Job
from thinktank.models.source import Source, SourceThinker
from thinktank.models.thinker import Thinker

router = APIRouter(prefix="/admin/thinkers", tags=["thinkers"])
templates = get_templates()


def _slugify(name: str) -> str:
    """Generate a URL slug from a thinker name."""
    slug = name.lower().strip().replace(" ", "-")
    slug = re.sub(r"[^a-z0-9\-]", "", slug)
    return slug


async def _unique_thinker_slug(session: AsyncSession, name: str) -> str:
    """Return a slug for ``name`` guaranteed to be unique across thinkers.

    ADMIN-REVIEW LO-02: ``_slugify`` alone produces collisions for common
    cases like "Jane Smith" (from an existing thinker) and "Jane Smith"
    (a new one with a different bio) -- the second insert would blow up
    on the ``slug UNIQUE`` constraint at commit time. The bulk import
    path in ``import_thinkers`` already skips duplicates, but the
    single-add and candidate-promote paths did not, so a retry with a
    suffix is the right behavior there.

    Appends ``-2``, ``-3``, ... until a free slug is found. Does not
    handle concurrent inserters racing on the same name -- the UNIQUE
    constraint still backs us in that case; this only keeps the common
    "same name, different people" path working without a 500.
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


async def _build_thinker_list(
    session: AsyncSession,
    q: str | None = None,
    tier: int | None = None,
    active: str | None = None,
) -> list[dict]:
    """Build thinker list with source counts and category names, applying filters."""
    # Source count subquery via junction
    source_count_sq = (
        select(
            SourceThinker.thinker_id,
            func.count(SourceThinker.source_id).label("source_count"),
        )
        .group_by(SourceThinker.thinker_id)
        .subquery()
    )

    # Main query. Eager-load categories -> category to avoid N+1 queries when
    # rendering the list: each thinker no longer triggers a SELECT per category.
    # ``populate_existing=True`` refreshes any session-resident rows (e.g.
    # ThinkerCategory objects inserted by edit_thinker() before this call) so
    # the nested .category relationship is guaranteed loaded.
    stmt = (
        select(Thinker, source_count_sq.c.source_count)
        .outerjoin(source_count_sq, Thinker.id == source_count_sq.c.thinker_id)
        .options(selectinload(Thinker.categories).selectinload(ThinkerCategory.category))
        .execution_options(populate_existing=True)
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
        # Category names are eager-loaded; no per-row SELECT required.
        category_names = [tc.category.name for tc in thinker.categories if tc.category]

        thinkers.append(
            {
                "id": str(thinker.id),
                "name": thinker.name,
                "slug": thinker.slug,
                "tier": thinker.tier,
                "active": thinker.active,
                "approval_status": thinker.approval_status,
                "source_count": source_count or 0,
                "category_names": ", ".join(category_names) if category_names else "",
            }
        )

    return thinkers


@router.get("/")
async def thinker_page(request: Request):
    """Render the thinker management page with HTMX-loaded list."""
    return templates.TemplateResponse(request, "thinkers.html")


@router.get("/partials/list")
async def thinker_list_partial(
    request: Request,
    session: AsyncSession = Depends(get_session),
    q: str | None = None,
    tier: int | None = None,
    active: str | None = None,
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

    slug = await _unique_thinker_slug(session, name)

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
        payload={"entity_type": "thinker", "target_id": str(thinker.id), "review_type": "thinker_approval"},
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


@router.get("/partials/bulk-import-form")
async def bulk_import_form_partial(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """HTML fragment: bulk import form with category multi-select."""
    result = await session.execute(select(Category).order_by(Category.name))
    categories = result.scalars().all()
    return templates.TemplateResponse(
        request,
        "partials/thinker_bulk_import_form.html",
        {"categories": categories},
    )


@router.post("/bulk-import")
async def bulk_import_thinkers(
    request: Request,
    session: AsyncSession = Depends(get_session),
    names: str = Form(...),
    tier: int = Form(...),
):
    """Create multiple thinkers from a newline-separated list of names."""
    form_data = await request.form()
    category_ids = form_data.getlist("category_ids")

    raw_names = [n.strip() for n in names.strip().splitlines() if n.strip()]
    if not raw_names:
        thinkers = await _build_thinker_list(session)
        return templates.TemplateResponse(
            request,
            "partials/thinker_list.html",
            {"thinkers": thinkers, "error": "No names provided."},
        )

    # Check for duplicate slugs against existing thinkers
    existing_slugs_result = await session.execute(select(Thinker.slug))
    existing_slugs = {row[0] for row in existing_slugs_result.all()}

    created = []
    skipped = []
    for name in raw_names:
        slug = _slugify(name)
        if slug in existing_slugs:
            skipped.append(name)
            continue
        existing_slugs.add(slug)

        thinker = Thinker(
            id=uuid.uuid4(),
            name=name,
            slug=slug,
            tier=tier,
            bio="",
            approval_status="awaiting_llm",
            active=True,
        )
        session.add(thinker)
        await session.flush()

        # Category associations
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

        # LLM approval job
        job = Job(
            id=uuid.uuid4(),
            job_type="llm_approval_check",
            payload={"entity_type": "thinker", "target_id": str(thinker.id), "review_type": "thinker_approval"},
            status="pending",
            priority=5,
        )
        session.add(job)
        created.append(name)

    await session.commit()

    # Build result message
    parts = []
    if created:
        parts.append(f"{len(created)} thinker(s) imported")
    if skipped:
        parts.append(f"{len(skipped)} skipped (already exist: {', '.join(skipped)})")
    msg = ". ".join(parts) + "."

    thinkers = await _build_thinker_list(session)
    return templates.TemplateResponse(
        request,
        "partials/thinker_list.html",
        {"thinkers": thinkers, "success": msg},
    )


@router.get("/candidates")
async def candidate_queue_page(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Candidate queue page showing pending and reviewed candidates."""
    result = await session.execute(select(CandidateThinker).order_by(CandidateThinker.appearance_count.desc()))
    candidates = result.scalars().all()
    return templates.TemplateResponse(
        request,
        "candidate_queue.html",
        {"candidates": candidates},
    )


@router.post("/candidates/{candidate_id}/promote")
async def promote_candidate(
    request: Request,
    candidate_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    reason: str = Form(""),
    principal: str = Depends(require_admin),
):
    """Promote a candidate to a full thinker with LLM approval job."""
    result = await session.execute(select(CandidateThinker).where(CandidateThinker.id == candidate_id))
    candidate = result.scalar_one_or_none()
    if candidate is None:
        raise HTTPException(status_code=404, detail="Candidate not found")

    # Create new thinker from candidate data
    slug = await _unique_thinker_slug(session, candidate.name)
    thinker = Thinker(
        id=uuid.uuid4(),
        name=candidate.name,
        slug=slug,
        tier=3,
        bio=f"Promoted from candidate. Reason: {reason}",
        approval_status="awaiting_llm",
        active=True,
    )
    session.add(thinker)
    await session.flush()

    # Create LLM approval job for the new thinker
    job = Job(
        id=uuid.uuid4(),
        job_type="llm_approval_check",
        payload={"entity_type": "thinker", "target_id": str(thinker.id), "review_type": "thinker_approval"},
        status="pending",
        priority=5,
    )
    session.add(job)

    # Update candidate status
    now = datetime.now(UTC)
    candidate.status = "promoted"
    candidate.thinker_id = thinker.id
    candidate.reviewed_by = principal
    candidate.reviewed_at = now

    await session.commit()

    # Re-render the candidate queue
    result = await session.execute(select(CandidateThinker).order_by(CandidateThinker.appearance_count.desc()))
    candidates = result.scalars().all()
    return templates.TemplateResponse(
        request,
        "partials/candidate_list.html",
        {"candidates": candidates, "success": f"'{candidate.name}' promoted to thinker. LLM approval queued."},
    )


@router.post("/candidates/{candidate_id}/reject")
async def reject_candidate(
    request: Request,
    candidate_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    reason: str = Form(""),
    principal: str = Depends(require_admin),
):
    """Reject a candidate with a reason."""
    result = await session.execute(select(CandidateThinker).where(CandidateThinker.id == candidate_id))
    candidate = result.scalar_one_or_none()
    if candidate is None:
        raise HTTPException(status_code=404, detail="Candidate not found")

    now = datetime.now(UTC)
    candidate.status = "rejected"
    candidate.reviewed_by = principal
    candidate.reviewed_at = now

    await session.commit()

    # Re-render the candidate queue
    result = await session.execute(select(CandidateThinker).order_by(CandidateThinker.appearance_count.desc()))
    candidates = result.scalars().all()
    return templates.TemplateResponse(
        request,
        "partials/candidate_list.html",
        {"candidates": candidates, "success": f"'{candidate.name}' rejected."},
    )


@router.get("/{thinker_id}")
async def thinker_detail_page(
    request: Request,
    thinker_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
):
    """Thinker detail page with HTMX-loaded sources, content, and reviews."""
    result = await session.execute(select(Thinker).where(Thinker.id == thinker_id))
    thinker = result.scalar_one_or_none()
    if thinker is None:
        raise HTTPException(status_code=404, detail="Thinker not found")

    # Resolve category names
    category_names = []
    for tc in thinker.categories:
        cat_result = await session.execute(select(Category.name).where(Category.id == tc.category_id))
        cat_name = cat_result.scalar_one_or_none()
        if cat_name:
            category_names.append(cat_name)

    return templates.TemplateResponse(
        request,
        "thinker_detail.html",
        {"thinker": thinker, "category_names": category_names},
    )


@router.get("/{thinker_id}/partials/sources")
async def thinker_sources_partial(
    request: Request,
    thinker_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
):
    """HTMX partial listing a thinker's sources via junction."""
    result = await session.execute(
        select(Source, SourceThinker.relationship_type)
        .join(SourceThinker, SourceThinker.source_id == Source.id)
        .where(SourceThinker.thinker_id == thinker_id)
        .order_by(Source.name)
    )
    sources = [{"source": row[0], "relationship_type": row[1]} for row in result.all()]
    return templates.TemplateResponse(
        request,
        "partials/thinker_sources.html",
        {"sources": sources},
    )


@router.get("/{thinker_id}/partials/content")
async def thinker_content_partial(
    request: Request,
    thinker_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
):
    """HTMX partial listing a thinker's recent content via content_thinkers junction."""
    result = await session.execute(
        select(Content)
        .join(ContentThinker, ContentThinker.content_id == Content.id)
        .where(ContentThinker.thinker_id == thinker_id)
        .order_by(Content.published_at.desc().nulls_last())
        .limit(20)
    )
    content_items = result.scalars().all()
    return templates.TemplateResponse(
        request,
        "partials/thinker_content.html",
        {"content_items": content_items},
    )


@router.get("/{thinker_id}/partials/reviews")
async def thinker_reviews_partial(
    request: Request,
    thinker_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
):
    """HTMX partial listing LLM review history for a thinker."""
    result = await session.execute(
        text(
            "SELECT id, review_type, decision, decision_reasoning, created_at "
            "FROM llm_reviews "
            "WHERE context_snapshot->>'thinker_id' = :tid "
            "ORDER BY created_at DESC LIMIT 20"
        ),
        {"tid": str(thinker_id)},
    )
    reviews = [
        {
            "id": str(row[0]),
            "review_type": row[1],
            "decision": row[2],
            "decision_reasoning": row[3],
            "created_at": row[4],
        }
        for row in result.fetchall()
    ]
    return templates.TemplateResponse(
        request,
        "partials/thinker_reviews.html",
        {"reviews": reviews},
    )


@router.post("/{thinker_id}/discover")
async def trigger_discovery(
    request: Request,
    thinker_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
):
    """Trigger discovery pipeline for a specific thinker (guest search + feed fetch)."""
    result = await session.execute(select(Thinker).where(Thinker.id == thinker_id))
    thinker = result.scalar_one_or_none()
    if thinker is None:
        raise HTTPException(status_code=404, detail="Thinker not found")

    job = Job(
        id=uuid.uuid4(),
        job_type="discover_thinker",
        payload={"thinker_id": str(thinker_id)},
        status="pending",
        priority=5,
    )
    session.add(job)
    await session.commit()

    return templates.TemplateResponse(
        request,
        "partials/discovery_result.html",
        {"thinker": thinker},
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
    await session.execute(delete(ThinkerCategory).where(ThinkerCategory.thinker_id == thinker_id))
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


@router.post("/{thinker_id}/resubmit-approval")
async def resubmit_approval(
    request: Request,
    thinker_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
):
    """Re-submit a thinker for LLM approval (for stuck awaiting_llm thinkers)."""
    result = await session.execute(select(Thinker).where(Thinker.id == thinker_id))
    thinker = result.scalar_one_or_none()
    if thinker is None:
        thinkers = await _build_thinker_list(session)
        return templates.TemplateResponse(
            request,
            "partials/thinker_list.html",
            {"thinkers": thinkers, "error": "Thinker not found."},
        )

    # Reset status and create a fresh approval job
    thinker.approval_status = "awaiting_llm"
    job = Job(
        id=uuid.uuid4(),
        job_type="llm_approval_check",
        payload={"entity_type": "thinker", "target_id": str(thinker.id), "review_type": "thinker_approval"},
        status="pending",
        priority=5,
    )
    session.add(job)
    await session.commit()

    thinkers = await _build_thinker_list(session)
    return templates.TemplateResponse(
        request,
        "partials/thinker_list.html",
        {"thinkers": thinkers, "success": f"LLM approval re-submitted for '{thinker.name}'."},
    )
