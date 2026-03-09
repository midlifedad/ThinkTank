"""Thinker CRUD endpoints."""

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from thinktank.api.dependencies import get_session
from thinktank.api.schemas import PaginatedResponse, ThinkerCreate, ThinkerResponse, ThinkerUpdate

from src.thinktank.models.thinker import Thinker

router = APIRouter(prefix="/api/thinkers", tags=["thinkers"])


@router.get("", response_model=PaginatedResponse[ThinkerResponse])
async def list_thinkers(
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    tier: Optional[int] = Query(default=None),
    status: Optional[str] = Query(default=None),
    category_id: Optional[uuid.UUID] = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> PaginatedResponse[ThinkerResponse]:
    """List thinkers with optional filters and pagination."""
    query = select(Thinker)

    if tier is not None:
        query = query.where(Thinker.tier == tier)
    if status is not None:
        query = query.where(Thinker.approval_status == status)
    if category_id is not None:
        from src.thinktank.models.category import ThinkerCategory

        query = query.join(ThinkerCategory).where(ThinkerCategory.category_id == category_id)

    # Count total
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await session.execute(count_query)
    total = total_result.scalar_one()

    # Paginate
    offset = (page - 1) * size
    query = query.offset(offset).limit(size)
    result = await session.execute(query)
    thinkers = result.scalars().unique().all()

    pages = (total + size - 1) // size

    return PaginatedResponse(
        items=[ThinkerResponse.model_validate(t) for t in thinkers],
        total=total,
        page=page,
        size=size,
        pages=pages,
    )


@router.get("/{thinker_id}", response_model=ThinkerResponse)
async def get_thinker(
    thinker_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
) -> ThinkerResponse:
    """Get a thinker by ID."""
    result = await session.execute(select(Thinker).where(Thinker.id == thinker_id))
    thinker = result.scalars().unique().first()
    if thinker is None:
        raise HTTPException(status_code=404, detail="Thinker not found")
    return ThinkerResponse.model_validate(thinker)


@router.post("", response_model=ThinkerResponse, status_code=201)
async def create_thinker(
    body: ThinkerCreate,
    session: AsyncSession = Depends(get_session),
) -> ThinkerResponse:
    """Create a new thinker."""
    thinker = Thinker(
        id=uuid.uuid4(),
        name=body.name,
        slug=body.slug,
        tier=body.tier,
        bio=body.bio,
    )
    session.add(thinker)
    await session.flush()
    await session.commit()
    await session.refresh(thinker)
    return ThinkerResponse.model_validate(thinker)


@router.patch("/{thinker_id}", response_model=ThinkerResponse)
async def update_thinker(
    thinker_id: uuid.UUID,
    body: ThinkerUpdate,
    session: AsyncSession = Depends(get_session),
) -> ThinkerResponse:
    """Update a thinker's fields."""
    result = await session.execute(select(Thinker).where(Thinker.id == thinker_id))
    thinker = result.scalars().unique().first()
    if thinker is None:
        raise HTTPException(status_code=404, detail="Thinker not found")

    update_data = body.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(thinker, field, value)

    await session.flush()
    await session.commit()
    await session.refresh(thinker)
    return ThinkerResponse.model_validate(thinker)
