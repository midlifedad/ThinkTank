"""Job queue status endpoint."""

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from thinktank.api.dependencies import get_session
from thinktank.api.schemas import JobStatusResponse
from thinktank.models.job import Job

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


@router.get("/status", response_model=JobStatusResponse)
async def get_job_status(
    session: AsyncSession = Depends(get_session),
) -> JobStatusResponse:
    """Get aggregated job queue status with counts by type and status, plus recent errors."""
    # Counts by type: {job_type: {status: count}}
    type_query = select(Job.job_type, Job.status, func.count().label("cnt")).group_by(Job.job_type, Job.status)
    type_result = await session.execute(type_query)
    by_type: dict[str, dict[str, int]] = {}
    for row in type_result.all():
        job_type, status, cnt = row
        if job_type not in by_type:
            by_type[job_type] = {}
        by_type[job_type][status] = cnt

    # Counts by status: {status: count}
    status_query = select(Job.status, func.count().label("cnt")).group_by(Job.status)
    status_result = await session.execute(status_query)
    by_status: dict[str, int] = {}
    for row in status_result.all():
        status, cnt = row
        by_status[status] = cnt

    # Recent errors: last 10 failed jobs
    error_query = (
        select(Job.job_type, Job.error, Job.error_category, Job.created_at)
        .where(Job.status == "failed")
        .order_by(Job.created_at.desc())
        .limit(10)
    )
    error_result = await session.execute(error_query)
    recent_errors = [
        {
            "job_type": row.job_type,
            "error": row.error,
            "error_category": row.error_category,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
        for row in error_result.all()
    ]

    return JobStatusResponse(
        by_type=by_type,
        by_status=by_status,
        recent_errors=recent_errors,
    )
