"""Pydantic v2 request/response models for ThinkTank REST API.

All response models use from_attributes=True for ORM compatibility.
All datetimes are timezone-naive per project convention.
"""

import uuid
from datetime import datetime
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict

T = TypeVar("T")


# ---------- Generic pagination ----------


class PaginatedResponse(BaseModel, Generic[T]):
    """Paginated response wrapper."""

    items: list[T]
    total: int
    page: int
    size: int
    pages: int


# ---------- Thinker schemas ----------


class ThinkerResponse(BaseModel):
    """Thinker response model."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    slug: str
    tier: int
    bio: str
    approval_status: str
    active: bool
    added_at: datetime
    primary_affiliation: str | None = None
    twitter_handle: str | None = None
    wikipedia_url: str | None = None
    personal_site: str | None = None


class ThinkerCreate(BaseModel):
    """Thinker creation request."""

    name: str
    slug: str
    tier: int
    bio: str


class ThinkerUpdate(BaseModel):
    """Thinker update request. All fields optional."""

    name: str | None = None
    slug: str | None = None
    tier: int | None = None
    bio: str | None = None
    approval_status: str | None = None
    active: bool | None = None
    primary_affiliation: str | None = None
    twitter_handle: str | None = None
    wikipedia_url: str | None = None
    personal_site: str | None = None


# ---------- Source schemas ----------


class SourceResponse(BaseModel):
    """Source response model."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    thinker_id: uuid.UUID | None = None
    source_type: str
    name: str
    url: str
    approval_status: str
    active: bool
    error_count: int
    created_at: datetime


# ---------- Content schemas ----------


class ContentResponse(BaseModel):
    """Content response model."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    source_id: uuid.UUID
    title: str
    content_type: str
    status: str
    canonical_url: str
    duration_seconds: int | None = None
    published_at: datetime | None = None
    discovered_at: datetime


# ---------- Job schemas ----------


class JobStatusResponse(BaseModel):
    """Aggregated job queue status."""

    by_type: dict[str, dict[str, int]]
    by_status: dict[str, int]
    recent_errors: list[dict[str, Any]]


# ---------- Config schemas ----------


class ConfigResponse(BaseModel):
    """System config response model."""

    model_config = ConfigDict(from_attributes=True)

    key: str
    value: Any
    set_by: str
    updated_at: datetime


class ConfigUpdate(BaseModel):
    """System config update request."""

    value: Any
    set_by: str
