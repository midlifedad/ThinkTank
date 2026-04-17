"""LLM decision panel router with human override functionality.

Provides the LLM panel page and auto-refreshing partials for pending approvals,
recent decisions, LLM status, and a human override endpoint with audit trail.
"""

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from thinktank.admin.dependencies import get_session, get_templates

router = APIRouter(prefix="/admin/llm", tags=["llm"])
templates = get_templates()


def _now() -> datetime:
    """Return current UTC time as timezone-naive datetime."""
    return datetime.now(UTC)


@router.get("/")
async def llm_panel(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Render the full LLM decision panel page."""
    # Get timeout config
    timeout_result = await session.execute(
        text("SELECT value FROM system_config WHERE key = 'llm_timeout_hours'")
    )
    timeout_row = timeout_result.fetchone()
    timeout_hours = timeout_row[0] if timeout_row else 2
    # Handle JSONB wrapping
    if isinstance(timeout_hours, dict):
        timeout_hours = timeout_hours.get("value", 2)

    return templates.TemplateResponse(
        request, "llm_panel.html", {"timeout_hours": timeout_hours},
    )


@router.get("/partials/pending")
async def pending_partial(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """HTML fragment: pending LLM reviews awaiting decision."""
    # Get timeout config
    timeout_result = await session.execute(
        text("SELECT value FROM system_config WHERE key = 'llm_timeout_hours'")
    )
    timeout_row = timeout_result.fetchone()
    timeout_hours = timeout_row[0] if timeout_row else 2
    if isinstance(timeout_hours, dict):
        timeout_hours = timeout_hours.get("value", 2)

    result = await session.execute(
        text(
            "SELECT id, review_type, context_snapshot, created_at "
            "FROM llm_reviews WHERE decision IS NULL "
            "ORDER BY created_at ASC"
        )
    )
    rows = result.fetchall()
    now = _now()

    pending = []
    for r in rows:
        created_at = r[3]
        time_waiting = now - created_at
        hours_waiting = time_waiting.total_seconds() / 3600
        is_timed_out = hours_waiting > float(timeout_hours)
        pending.append({
            "id": r[0],
            "review_type": r[1],
            "context_snapshot": r[2] or {},
            "created_at": created_at,
            "hours_waiting": f"{hours_waiting:.1f}",
            "is_timed_out": is_timed_out,
        })

    return templates.TemplateResponse(
        request, "partials/llm_pending.html", {"pending": pending},
    )


@router.get("/partials/recent")
async def recent_partial(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """HTML fragment: last 20 completed LLM reviews."""
    result = await session.execute(
        text(
            "SELECT id, review_type, decision, decision_reasoning, "
            "tokens_used, overridden_by, created_at "
            "FROM llm_reviews WHERE decision IS NOT NULL "
            "ORDER BY created_at DESC LIMIT 20"
        )
    )
    rows = result.fetchall()
    recent = [
        {
            "id": r[0],
            "review_type": r[1],
            "decision": r[2],
            "reasoning": (r[3] or "")[:100],
            "tokens_used": r[4] or 0,
            "overridden": r[5] is not None,
            "created_at": r[6],
        }
        for r in rows
    ]
    return templates.TemplateResponse(
        request, "partials/llm_recent.html", {"recent": recent},
    )


@router.get("/partials/status")
async def status_partial(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """HTML fragment: LLM system status summary."""
    pending_result = await session.execute(
        text("SELECT COUNT(*) FROM llm_reviews WHERE decision IS NULL")
    )
    pending_count = pending_result.scalar() or 0

    tokens_result = await session.execute(
        text(
            "SELECT COALESCE(SUM(tokens_used), 0) FROM llm_reviews "
            "WHERE created_at > LOCALTIMESTAMP - INTERVAL '24 hours'"
        )
    )
    total_tokens = tokens_result.scalar() or 0

    override_result = await session.execute(
        text("SELECT COUNT(*) FROM llm_reviews WHERE overridden_by IS NOT NULL")
    )
    override_count = override_result.scalar() or 0

    return templates.TemplateResponse(
        request, "partials/llm_status.html",
        {
            "pending_count": pending_count,
            "total_tokens": total_tokens,
            "override_count": override_count,
        },
    )


@router.post("/override/{review_id}")
async def override_decision(
    review_id: UUID,
    request: Request,
    session: AsyncSession = Depends(get_session),
    override_decision: str = Form(...),
    override_reasoning: str = Form(...),
    admin_username: str = Form("admin"),
):
    """Process human override of an LLM decision.

    Updates the LLM review and applies the override to the target entity
    (thinker, source, or candidate) in the same transaction.
    """
    from thinktank.models.candidate import CandidateThinker
    from thinktank.models.review import LLMReview
    from thinktank.models.source import Source
    from thinktank.models.thinker import Thinker

    review = await session.get(LLMReview, review_id)
    if not review:
        raise HTTPException(
            status_code=404,
            detail=f"LLM review {review_id} not found",
        )

    # Update the review record
    review.decision = override_decision
    review.overridden_by = admin_username
    review.overridden_at = _now()
    review.override_reasoning = override_reasoning

    # Map form values (the select offers approve/reject) to the canonical
    # status values each entity uses. Writing the raw form value silently
    # poisoned rows with invalid approval_status pre-Phase-4; the CHECK
    # constraint now surfaces it.
    _APPROVAL_MAP = {"approve": "approved", "reject": "rejected"}
    _CANDIDATE_MAP = {"approve": "promoted", "reject": "rejected"}

    # Apply override to the target entity
    snapshot = review.context_snapshot or {}
    if review.review_type == "thinker_approval" and "thinker_id" in snapshot:
        thinker = await session.get(Thinker, UUID(snapshot["thinker_id"]))
        if thinker:
            thinker.approval_status = _APPROVAL_MAP.get(override_decision, override_decision)
    elif review.review_type == "source_approval" and "source_id" in snapshot:
        source = await session.get(Source, UUID(snapshot["source_id"]))
        if source:
            source.approval_status = _APPROVAL_MAP.get(override_decision, override_decision)
    elif review.review_type == "candidate_review" and "candidate_id" in snapshot:
        candidate = await session.get(CandidateThinker, UUID(snapshot["candidate_id"]))
        if candidate:
            candidate.status = _CANDIDATE_MAP.get(override_decision, override_decision)

    await session.commit()

    # Re-render the pending partial to reflect the change
    result = await session.execute(
        text(
            "SELECT id, review_type, context_snapshot, created_at "
            "FROM llm_reviews WHERE decision IS NULL "
            "ORDER BY created_at ASC"
        )
    )
    rows = result.fetchall()
    now = _now()

    # Get timeout config
    timeout_result = await session.execute(
        text("SELECT value FROM system_config WHERE key = 'llm_timeout_hours'")
    )
    timeout_row = timeout_result.fetchone()
    timeout_hours = timeout_row[0] if timeout_row else 2
    if isinstance(timeout_hours, dict):
        timeout_hours = timeout_hours.get("value", 2)

    pending = []
    for r in rows:
        created_at = r[3]
        time_waiting = now - created_at
        hours_waiting = time_waiting.total_seconds() / 3600
        is_timed_out = hours_waiting > float(timeout_hours)
        pending.append({
            "id": r[0],
            "review_type": r[1],
            "context_snapshot": r[2] or {},
            "created_at": created_at,
            "hours_waiting": f"{hours_waiting:.1f}",
            "is_timed_out": is_timed_out,
        })

    return templates.TemplateResponse(
        request, "partials/llm_pending.html", {"pending": pending},
    )
