"""Thinker, ThinkerProfile, and ThinkerMetrics models.

Spec references: Section 3.2 (thinkers), Section 3.4 (thinker_profiles), Section 3.5 (thinker_metrics).
"""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from thinktank.models.base import Base, uuid_pk

if TYPE_CHECKING:
    from thinktank.models.category import ThinkerCategory
    from thinktank.models.source import Source


class Thinker(Base):
    """A recognized thinker/expert whose content is ingested."""

    __tablename__ = "thinkers"

    id: Mapped[uuid_pk]
    name: Mapped[str] = mapped_column(sa.Text)
    slug: Mapped[str] = mapped_column(sa.Text, unique=True)
    tier: Mapped[int] = mapped_column(sa.SmallInteger)
    bio: Mapped[str] = mapped_column(sa.Text)
    primary_affiliation: Mapped[Optional[str]] = mapped_column(sa.Text, nullable=True)
    twitter_handle: Mapped[Optional[str]] = mapped_column(sa.Text, nullable=True)
    wikipedia_url: Mapped[Optional[str]] = mapped_column(sa.Text, nullable=True)
    personal_site: Mapped[Optional[str]] = mapped_column(sa.Text, nullable=True)
    approval_status: Mapped[str] = mapped_column(sa.Text, server_default="pending_llm")
    approved_backfill_days: Mapped[Optional[int]] = mapped_column(sa.Integer, nullable=True)
    approved_source_types: Mapped[Optional[list[str]]] = mapped_column(
        ARRAY(sa.Text),
        nullable=True,
    )
    active: Mapped[bool] = mapped_column(sa.Boolean, server_default=sa.text("true"))
    added_at: Mapped[datetime] = mapped_column(server_default=sa.text("NOW()"))
    last_refreshed: Mapped[Optional[datetime]] = mapped_column(nullable=True)

    # Relationships
    sources: Mapped[list["Source"]] = relationship(
        back_populates="thinker",
        lazy="selectin",
    )
    profiles: Mapped[list["ThinkerProfile"]] = relationship(
        back_populates="thinker",
        lazy="selectin",
    )
    metrics: Mapped[list["ThinkerMetrics"]] = relationship(
        back_populates="thinker",
        lazy="selectin",
    )
    categories: Mapped[list["ThinkerCategory"]] = relationship(
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<Thinker(slug={self.slug!r}, tier={self.tier})>"


class ThinkerProfile(Base):
    """Extended profile data for a thinker (education, positions, works, awards)."""

    __tablename__ = "thinker_profiles"

    id: Mapped[uuid_pk]
    thinker_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("thinkers.id"))
    education: Mapped[dict] = mapped_column(JSONB, server_default=sa.text("'[]'::jsonb"))
    positions_held: Mapped[dict] = mapped_column(JSONB, server_default=sa.text("'[]'::jsonb"))
    notable_works: Mapped[dict] = mapped_column(JSONB, server_default=sa.text("'[]'::jsonb"))
    awards: Mapped[dict] = mapped_column(JSONB, server_default=sa.text("'[]'::jsonb"))
    updated_at: Mapped[datetime] = mapped_column(server_default=sa.text("NOW()"))

    # Relationships
    thinker: Mapped["Thinker"] = relationship(back_populates="profiles")

    def __repr__(self) -> str:
        return f"<ThinkerProfile(thinker_id={self.thinker_id})>"


class ThinkerMetrics(Base):
    """Platform-specific metrics snapshot for a thinker."""

    __tablename__ = "thinker_metrics"

    id: Mapped[uuid_pk]
    thinker_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("thinkers.id"))
    platform: Mapped[str] = mapped_column(sa.Text)
    handle: Mapped[str] = mapped_column(sa.Text)
    followers: Mapped[int] = mapped_column(sa.BigInteger)
    avg_views: Mapped[int] = mapped_column(sa.BigInteger)
    post_count: Mapped[int] = mapped_column(sa.Integer)
    verified: Mapped[bool] = mapped_column(sa.Boolean)
    snapshotted_at: Mapped[datetime] = mapped_column(server_default=sa.text("NOW()"))

    # Relationships
    thinker: Mapped["Thinker"] = relationship(back_populates="metrics")

    def __repr__(self) -> str:
        return f"<ThinkerMetrics(thinker_id={self.thinker_id}, platform={self.platform!r})>"
