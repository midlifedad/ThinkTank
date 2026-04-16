"""Content listing with pagination."""

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from thinktank.api.dependencies import get_session
from thinktank.api.schemas import ContentResponse, PaginatedResponse

from thinktank.models.content import Content

router = APIRouter(prefix="/api/content", tags=["content"])


@router.get("", response_model=PaginatedResponse[ContentResponse])
async def list_content(
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    source_id: Optional[uuid.UUID] = Query(default=None),
    thinker_id: Optional[uuid.UUID] = Query(default=None),
    status: Optional[str] = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> PaginatedResponse[ContentResponse]:
    """List content with optional filters and pagination."""
    query = select(Content)

    if source_id is not None:
        query = query.where(Content.source_id == source_id)
    if thinker_id is not None:
        query = query.where(Content.source_owner_id == thinker_id)
    if status is not None:
        query = query.where(Content.status == status)

    # Count total
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await session.execute(count_query)
    total = total_result.scalar_one()

    # Paginate
    offset = (page - 1) * size
    query = query.offset(offset).limit(size)
    result = await session.execute(query)
    items = result.scalars().unique().all()

    pages = (total + size - 1) // size

    return PaginatedResponse(
        items=[ContentResponse.model_validate(c) for c in items],
        total=total,
        page=page,
        size=size,
        pages=pages,
    )
