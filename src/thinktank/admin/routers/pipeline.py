"""Pipeline control router for admin dashboard.

Provides a filterable, paginated job queue browser, manual job triggers,
retry/cancel actions, job detail view, and recurring task scheduler editor.
Operators can monitor processing, diagnose failures, manage individual job
lifecycle, and configure recurring task schedules.
"""

import json
import uuid
from datetime import datetime, timedelta, UTC
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy import text as sa_text

from src.thinktank.models.config_table import SystemConfig
from src.thinktank.models.job import Job
from thinktank.admin.dependencies import get_session, get_templates

router = APIRouter(prefix="/admin/pipeline", tags=["pipeline"])
templates = get_templates()

# Job types allowed for manual triggering
ALLOWED_TRIGGER_TYPES = {
    "refresh_due_sources",
    "scan_for_candidates",
    "discover_guests_podcastindex",
}

# All known job types for the filter dropdown
KNOWN_JOB_TYPES = [
    "fetch_podcast_feed",
    "refresh_due_sources",
    "tag_content_thinkers",
    "process_content",
    "llm_approval_check",
    "scan_for_candidates",
    "discover_guests_podcastindex",
    "rollup_api_usage",
]

# Configurable scheduled tasks
SCHEDULED_TASKS = [
    {"key": "refresh_due_sources", "label": "Refresh Due Sources", "default_hours": 1, "job_type": "refresh_due_sources"},
    {"key": "scan_for_candidates", "label": "Scan for Candidates", "default_hours": 24, "job_type": "scan_for_candidates"},
    {"key": "llm_health_check", "label": "LLM Health Check", "default_hours": 6, "job_type": None},
    {"key": "llm_daily_digest", "label": "LLM Daily Digest", "default_hours": 24, "job_type": None},
    {"key": "llm_weekly_audit", "label": "LLM Weekly Audit", "default_hours": 168, "job_type": None},
]

# Lookup for quick validation
_SCHEDULED_TASK_MAP = {t["key"]: t for t in SCHEDULED_TASKS}

PAGE_SIZE = 25


def _utcnow() -> datetime:
    """Timezone-naive UTC now, matching TIMESTAMP WITHOUT TIME ZONE columns."""
    return datetime.now(UTC).replace(tzinfo=None)


@router.get("/")
async def pipeline_page(request: Request):
    """Render the pipeline control page. Job list loaded via HTMX partial."""
    return templates.TemplateResponse(
        request,
        "pipeline.html",
        {"job_types": KNOWN_JOB_TYPES},
    )


@router.get("/partials/jobs")
async def job_list_partial(
    request: Request,
    session: AsyncSession = Depends(get_session),
    status: Optional[str] = None,
    job_type: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    page: int = 1,
):
    """HTMX partial: paginated, filtered job list."""
    query = select(Job)
    count_query = select(func.count(Job.id))

    # Apply filters
    if status and status.strip():
        query = query.where(Job.status == status)
        count_query = count_query.where(Job.status == status)

    if job_type and job_type.strip():
        query = query.where(Job.job_type == job_type)
        count_query = count_query.where(Job.job_type == job_type)

    if date_from and date_from.strip():
        try:
            dt_from = datetime.fromisoformat(date_from)
            query = query.where(Job.created_at >= dt_from)
            count_query = count_query.where(Job.created_at >= dt_from)
        except ValueError:
            pass

    if date_to and date_to.strip():
        try:
            dt_to = datetime.fromisoformat(date_to) + timedelta(days=1)
            query = query.where(Job.created_at < dt_to)
            count_query = count_query.where(Job.created_at < dt_to)
        except ValueError:
            pass

    # Total count for pagination
    total_result = await session.execute(count_query)
    total_count = total_result.scalar() or 0
    total_pages = max(1, (total_count + PAGE_SIZE - 1) // PAGE_SIZE)

    # Clamp page
    if page < 1:
        page = 1
    if page > total_pages:
        page = total_pages

    # Fetch page
    offset = (page - 1) * PAGE_SIZE
    query = query.order_by(Job.created_at.desc()).offset(offset).limit(PAGE_SIZE)
    result = await session.execute(query)
    jobs = result.scalars().all()

    return templates.TemplateResponse(
        request,
        "partials/job_list.html",
        {
            "jobs": jobs,
            "page": page,
            "total_pages": total_pages,
            "total_count": total_count,
            "filters": {
                "status": status or "",
                "job_type": job_type or "",
                "date_from": date_from or "",
                "date_to": date_to or "",
            },
        },
    )


@router.get("/partials/activity")
async def activity_feed_partial(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """HTMX partial: live activity feed showing the 20 most recent job state changes."""
    query = (
        select(Job)
        .where(Job.status.in_(["running", "done", "failed", "retrying"]))
        .order_by(
            func.coalesce(Job.completed_at, Job.started_at, Job.last_error_at, Job.created_at).desc()
        )
        .limit(20)
    )
    result = await session.execute(query)
    jobs = result.scalars().all()

    return templates.TemplateResponse(
        request,
        "partials/activity_feed.html",
        {"jobs": jobs},
    )


@router.get("/jobs/{job_id}")
async def job_detail(
    request: Request,
    job_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
):
    """Job detail view showing all job fields plus linked LLM review if applicable."""
    result = await session.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # Format payload as pretty JSON string
    payload_str = json.dumps(job.payload, indent=2, default=str) if job.payload else "{}"

    # Look up linked LLM review for llm_approval_check jobs
    llm_review = None
    if job.job_type == "llm_approval_check" and job.payload:
        entity_id = job.payload.get("entity_id")
        review_type = job.payload.get("review_type")
        if entity_id and review_type:
            review_result = await session.execute(
                sa_text(
                    "SELECT id, review_type, decision, decision_reasoning, model, "
                    "tokens_used, duration_ms, created_at, prompt_used, llm_response "
                    "FROM llm_reviews "
                    "WHERE review_type = :rt "
                    "AND context_snapshot->>'thinker_id' = :eid "
                    "ORDER BY created_at DESC LIMIT 1"
                ),
                {"rt": review_type, "eid": entity_id},
            )
            row = review_result.fetchone()
            if row:
                llm_review = {
                    "id": str(row[0]),
                    "review_type": row[1],
                    "decision": row[2],
                    "reasoning": row[3],
                    "model": row[4],
                    "tokens_used": row[5],
                    "duration_ms": row[6],
                    "created_at": row[7],
                    "prompt_used": row[8],
                    "llm_response": row[9],
                }

    return templates.TemplateResponse(
        request,
        "partials/job_detail.html",
        {"job": job, "payload_str": payload_str, "llm_review": llm_review},
    )


@router.post("/trigger/{job_type}")
async def trigger_job(
    request: Request,
    job_type: str,
    session: AsyncSession = Depends(get_session),
    thinker_id: Optional[str] = Form(None),
):
    """Manually trigger a pipeline job."""
    if job_type not in ALLOWED_TRIGGER_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid job type: {job_type}. Allowed: {', '.join(sorted(ALLOWED_TRIGGER_TYPES))}",
        )

    payload: dict = {"triggered_by": "admin"}
    if thinker_id and thinker_id.strip():
        payload["thinker_id"] = thinker_id.strip()

    new_job = Job(
        id=uuid.uuid4(),
        job_type=job_type,
        payload=payload,
        status="pending",
        priority=5,
        attempts=0,
        max_attempts=3,
    )
    session.add(new_job)
    await session.commit()

    return templates.TemplateResponse(
        request,
        "partials/trigger_result.html",
        {"success": True, "message": f"Job {job_type} queued successfully", "job_id": str(new_job.id)},
    )


@router.post("/jobs/{job_id}/retry")
async def retry_job(
    request: Request,
    job_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
):
    """Retry a failed job by creating a new pending job with same type and payload."""
    result = await session.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.status != "failed":
        response = templates.TemplateResponse(
            request,
            "partials/trigger_result.html",
            {"success": False, "message": f"Cannot retry job with status '{job.status}'. Only failed jobs can be retried."},
        )
        return response

    # Create new job copying type and payload
    new_job = Job(
        id=uuid.uuid4(),
        job_type=job.job_type,
        payload=dict(job.payload) if job.payload else {},
        status="pending",
        priority=5,
        attempts=0,
        max_attempts=3,
    )
    session.add(new_job)
    await session.commit()

    response = templates.TemplateResponse(
        request,
        "partials/trigger_result.html",
        {"success": True, "message": f"Retry job created for {job.job_type}", "job_id": str(new_job.id)},
    )
    response.headers["HX-Trigger"] = "refreshJobList"
    return response


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(
    request: Request,
    job_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
):
    """Cancel a pending job by setting its status to cancelled."""
    result = await session.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.status != "pending":
        response = templates.TemplateResponse(
            request,
            "partials/trigger_result.html",
            {"success": False, "message": f"Cannot cancel job with status '{job.status}'. Only pending jobs can be cancelled."},
        )
        return response

    job.status = "cancelled"
    await session.commit()

    response = templates.TemplateResponse(
        request,
        "partials/trigger_result.html",
        {"success": True, "message": f"Job {job.job_type} cancelled"},
    )
    response.headers["HX-Trigger"] = "refreshJobList"
    return response


# ---------------------------------------------------------------------------
# Recurring Task Scheduler Endpoints
# ---------------------------------------------------------------------------


async def _build_scheduler_context(
    session: AsyncSession,
) -> list[dict]:
    """Build the list of task configs for the scheduler editor template."""
    tasks = []
    for task_def in SCHEDULED_TASKS:
        config_key = f"scheduler_{task_def['key']}"
        result = await session.execute(
            select(SystemConfig.value).where(SystemConfig.key == config_key)
        )
        raw = result.scalar_one_or_none()

        if raw and isinstance(raw, dict):
            frequency_hours = raw.get("frequency_hours", task_def["default_hours"])
            enabled = raw.get("enabled", True)
            last_run_at = raw.get("last_run_at")
            next_run_at = raw.get("next_run_at")
        else:
            frequency_hours = task_def["default_hours"]
            enabled = True
            last_run_at = None
            next_run_at = None

        # Parse ISO datetime strings from JSONB
        if isinstance(last_run_at, str):
            try:
                last_run_at = datetime.fromisoformat(last_run_at)
            except ValueError:
                last_run_at = None
        if isinstance(next_run_at, str):
            try:
                next_run_at = datetime.fromisoformat(next_run_at)
            except ValueError:
                next_run_at = None

        tasks.append({
            "key": task_def["key"],
            "label": task_def["label"],
            "frequency_hours": frequency_hours,
            "enabled": enabled,
            "last_run_at": last_run_at,
            "next_run_at": next_run_at,
            "has_job_type": task_def["job_type"] is not None,
        })
    return tasks


@router.get("/partials/scheduler")
async def scheduler_editor_partial(
    request: Request,
    session: AsyncSession = Depends(get_session),
    success: Optional[str] = None,
    info: Optional[str] = None,
):
    """HTMX partial: recurring task scheduler editor."""
    tasks = await _build_scheduler_context(session)
    return templates.TemplateResponse(
        request,
        "partials/scheduler_editor.html",
        {"tasks": tasks, "success": success, "info": info},
    )


@router.post("/scheduler/{task_key}/save")
async def scheduler_save(
    request: Request,
    task_key: str,
    session: AsyncSession = Depends(get_session),
    frequency_hours: int = Form(...),
):
    """Save the frequency for a scheduled task."""
    if task_key not in _SCHEDULED_TASK_MAP:
        raise HTTPException(status_code=404, detail=f"Unknown task: {task_key}")

    task_def = _SCHEDULED_TASK_MAP[task_key]
    frequency_hours = max(1, frequency_hours)
    config_key = f"scheduler_{task_key}"
    now = _utcnow()

    result = await session.execute(
        select(SystemConfig).where(SystemConfig.key == config_key)
    )
    existing = result.scalar_one_or_none()

    if existing:
        val = dict(existing.value) if isinstance(existing.value, dict) else {}
        val["frequency_hours"] = frequency_hours
        # Recalculate next_run_at
        last_run = val.get("last_run_at")
        if last_run and isinstance(last_run, str):
            try:
                last_dt = datetime.fromisoformat(last_run)
                val["next_run_at"] = (last_dt + timedelta(hours=frequency_hours)).isoformat()
            except ValueError:
                val["next_run_at"] = (now + timedelta(hours=frequency_hours)).isoformat()
        else:
            val["next_run_at"] = (now + timedelta(hours=frequency_hours)).isoformat()
        existing.value = val
        existing.set_by = "admin"
        existing.updated_at = now
    else:
        session.add(SystemConfig(
            key=config_key,
            value={
                "frequency_hours": frequency_hours,
                "enabled": True,
                "last_run_at": None,
                "next_run_at": (now + timedelta(hours=frequency_hours)).isoformat(),
            },
            set_by="admin",
            updated_at=now,
        ))

    await session.commit()

    tasks = await _build_scheduler_context(session)
    return templates.TemplateResponse(
        request,
        "partials/scheduler_editor.html",
        {"tasks": tasks, "success": f"Frequency for {task_def['label']} saved."},
    )


@router.post("/scheduler/{task_key}/toggle")
async def scheduler_toggle(
    request: Request,
    task_key: str,
    session: AsyncSession = Depends(get_session),
):
    """Toggle enabled/disabled for a scheduled task."""
    if task_key not in _SCHEDULED_TASK_MAP:
        raise HTTPException(status_code=404, detail=f"Unknown task: {task_key}")

    task_def = _SCHEDULED_TASK_MAP[task_key]
    config_key = f"scheduler_{task_key}"
    now = _utcnow()

    result = await session.execute(
        select(SystemConfig).where(SystemConfig.key == config_key)
    )
    existing = result.scalar_one_or_none()

    if existing:
        val = dict(existing.value) if isinstance(existing.value, dict) else {}
        was_enabled = val.get("enabled", True)
        val["enabled"] = not was_enabled
        # If being enabled, set next_run_at to now + frequency
        if not was_enabled:
            freq = val.get("frequency_hours", task_def["default_hours"])
            val["next_run_at"] = (now + timedelta(hours=freq)).isoformat()
        existing.value = val
        existing.set_by = "admin"
        existing.updated_at = now
    else:
        # Default is enabled=True, so toggling creates with enabled=False
        session.add(SystemConfig(
            key=config_key,
            value={
                "frequency_hours": task_def["default_hours"],
                "enabled": False,
                "last_run_at": None,
                "next_run_at": None,
            },
            set_by="admin",
            updated_at=now,
        ))

    await session.commit()

    tasks = await _build_scheduler_context(session)
    return templates.TemplateResponse(
        request,
        "partials/scheduler_editor.html",
        {"tasks": tasks, "success": f"{task_def['label']} toggled."},
    )


@router.post("/scheduler/{task_key}/run-now")
async def scheduler_run_now(
    request: Request,
    task_key: str,
    session: AsyncSession = Depends(get_session),
):
    """Run Now: create a job for pipeline tasks, show info for LLM tasks."""
    if task_key not in _SCHEDULED_TASK_MAP:
        raise HTTPException(status_code=404, detail=f"Unknown task: {task_key}")

    task_def = _SCHEDULED_TASK_MAP[task_key]
    config_key = f"scheduler_{task_key}"
    now = _utcnow()

    if task_def["job_type"] is None:
        # LLM tasks -- no job created
        tasks = await _build_scheduler_context(session)
        return templates.TemplateResponse(
            request,
            "partials/scheduler_editor.html",
            {"tasks": tasks, "info": f"{task_def['label']}: LLM tasks run on their internal schedule in the worker loop."},
        )

    # Create a pending job
    new_job = Job(
        id=uuid.uuid4(),
        job_type=task_def["job_type"],
        payload={"triggered_by": "admin_scheduler_run_now"},
        status="pending",
        priority=5,
        attempts=0,
        max_attempts=3,
    )
    session.add(new_job)

    # Update scheduler config: last_run_at = now, next_run_at = now + frequency
    result = await session.execute(
        select(SystemConfig).where(SystemConfig.key == config_key)
    )
    existing = result.scalar_one_or_none()

    if existing:
        val = dict(existing.value) if isinstance(existing.value, dict) else {}
        freq = val.get("frequency_hours", task_def["default_hours"])
        val["last_run_at"] = now.isoformat()
        val["next_run_at"] = (now + timedelta(hours=freq)).isoformat()
        existing.value = val
        existing.set_by = "admin"
        existing.updated_at = now
    else:
        session.add(SystemConfig(
            key=config_key,
            value={
                "frequency_hours": task_def["default_hours"],
                "enabled": True,
                "last_run_at": now.isoformat(),
                "next_run_at": (now + timedelta(hours=task_def["default_hours"])).isoformat(),
            },
            set_by="admin",
            updated_at=now,
        ))

    await session.commit()

    tasks = await _build_scheduler_context(session)
    return templates.TemplateResponse(
        request,
        "partials/scheduler_editor.html",
        {"tasks": tasks, "success": f"{task_def['label']} triggered. Job queued."},
    )
