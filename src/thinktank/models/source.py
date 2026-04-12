"""Source and SourceThinker (junction) models.

Spec reference: Section 3.6 (sources).
"""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.thinktank.models.base import Base, uuid_pk

if TYPE_CHECKING:
    from src.thinktank.models.content import Content
    from src.thinktank.models.thinker import Thinker


class Source(Base):
    """A content source (RSS feed, YouTube channel, etc.).

    Sources are first-class entities independent of thinkers.
    The many-to-many relationship with thinkers is managed via
    the source_thinkers junction table.
    """

    __tablename__ = "sources"

    id: Mapped[uuid_pk]
    thinker_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        sa.ForeignKey("thinkers.id"), nullable=True
    )  # DEPRECATED — use source_thinkers junction
    source_type: Mapped[str] = mapped_column(sa.Text)
    name: Mapped[str] = mapped_column(sa.Text)
    slug: Mapped[Optional[str]] = mapped_column(sa.Text, unique=True, nullable=True)
    url: Mapped[str] = mapped_column(sa.Text, unique=True)
    external_id: Mapped[Optional[str]] = mapped_column(sa.Text, nullable=True)
    tier: Mapped[int] = mapped_column(sa.SmallInteger, server_default=sa.text("2"))
    description: Mapped[Optional[str]] = mapped_column(sa.Text, nullable=True)
    host_name: Mapped[Optional[str]] = mapped_column(sa.Text, nullable=True)
    config: Mapped[dict] = mapped_column(JSONB, server_default=sa.text("'{}'::jsonb"))
    approval_status: Mapped[str] = mapped_column(sa.Text, server_default="pending_llm")
    approved_backfill_days: Mapped[Optional[int]] = mapped_column(sa.Integer, nullable=True)
    backfill_complete: Mapped[bool] = mapped_column(sa.Boolean, server_default=sa.text("false"))
    refresh_interval_hours: Mapped[Optional[int]] = mapped_column(sa.Integer, nullable=True)
    last_fetched: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    item_count: Mapped[int] = mapped_column(sa.Integer, server_default=sa.text("0"))
    active: Mapped[bool] = mapped_column(sa.Boolean, server_default=sa.text("true"))
    error_count: Mapped[int] = mapped_column(sa.Integer, server_default=sa.text("0"))
    created_at: Mapped[datetime] = mapped_column(server_default=sa.text("NOW()"))

    # Relationships
    thinker: Mapped[Optional["Thinker"]] = relationship(
        back_populates="sources"
    )  # DEPRECATED
    source_thinkers: Mapped[list["SourceThinker"]] = relationship(
        back_populates="source",
        lazy="selectin",
    )
    content: Mapped[list["Content"]] = relationship(
        back_populates="source",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<Source(name={self.name!r}, type={self.source_type!r})>"


class SourceThinker(Base):
    """Junction table linking sources to thinkers with relationship type.

    Composite primary key on (source_id, thinker_id).
    """

    __tablename__ = "source_thinkers"

    source_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("sources.id"),
        primary_key=True,
    )
    thinker_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("thinkers.id"),
        primary_key=True,
    )
    relationship_type: Mapped[str] = mapped_column(sa.Text)
    added_at: Mapped[datetime] = mapped_column(server_default=sa.text("NOW()"))

    # Relationships
    source: Mapped["Source"] = relationship(back_populates="source_thinkers")
    thinker: Mapped["Thinker"] = relationship()

    def __repr__(self) -> str:
        return (
            f"<SourceThinker(source={self.source_id}, "
            f"thinker={self.thinker_id}, type={self.relationship_type!r})>"
        )
