"""Pipeline control router for admin dashboard.

Provides a filterable, paginated job queue browser, manual job triggers,
retry/cancel actions, and job detail view. Operators can monitor processing,
diagnose failures, and manage individual job lifecycle.
"""

import json
import uuid
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

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

PAGE_SIZE = 25


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


@router.get("/jobs/{job_id}")
async def job_detail(
    request: Request,
    job_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
):
    """Job detail view showing all job fields."""
    result = await session.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # Format payload as pretty JSON string
    payload_str = json.dumps(job.payload, indent=2, default=str) if job.payload else "{}"

    return templates.TemplateResponse(
        request,
        "partials/job_detail.html",
        {"job": job, "payload_str": payload_str},
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
