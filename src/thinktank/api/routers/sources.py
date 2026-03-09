"""Source listing endpoints."""

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from thinktank.api.dependencies import get_session
from thinktank.api.schemas import PaginatedResponse, SourceResponse

from src.thinktank.models.source import Source

router = APIRouter(prefix="/api/sources", tags=["sources"])


@router.get("", response_model=PaginatedResponse[SourceResponse])
async def list_sources(
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    thinker_id: Optional[uuid.UUID] = Query(default=None),
    approval_status: Optional[str] = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> PaginatedResponse[SourceResponse]:
    """List sources with optional filters and pagination."""
    query = select(Source)

    if thinker_id is not None:
        query = query.where(Source.thinker_id == thinker_id)
    if approval_status is not None:
        query = query.where(Source.approval_status == approval_status)

    # Count total
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await session.execute(count_query)
    total = total_result.scalar_one()

    # Paginate
    offset = (page - 1) * size
    query = query.offset(offset).limit(size)
    result = await session.execute(query)
    sources = result.scalars().unique().all()

    pages = (total + size - 1) // size

    return PaginatedResponse(
        items=[SourceResponse.model_validate(s) for s in sources],
        total=total,
        page=page,
        size=size,
        pages=pages,
    )
