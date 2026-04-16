"""Base classes and common types for SQLAlchemy 2.0 async models."""

import uuid
from datetime import datetime
from typing import Annotated

import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Reusable annotated type for UUID primary keys
uuid_pk = Annotated[
    uuid.UUID,
    mapped_column(primary_key=True, default=uuid.uuid4),
]

# Reusable annotated type for created_at timestamps.
# DATA-REVIEW H4 / HANDLERS-REVIEW LO-06: every timestamp column is
# TIMESTAMPTZ so stored values always include explicit UTC offset.
created_at_col = Annotated[
    datetime,
    mapped_column(sa.DateTime(timezone=True), server_default=text("NOW()")),
]


class Base(AsyncAttrs, DeclarativeBase):
    """Base class for all ThinkTank models.

    AsyncAttrs enables async relationship access (await model.awaitable_attrs.relationship).
    """

    pass


class TimestampMixin:
    """Mixin providing a created_at timestamp column with server-side default."""

    created_at: Mapped[created_at_col]
