"""Thinker, ThinkerProfile, and ThinkerMetrics models.

Spec references: Section 3.2 (thinkers), Section 3.4 (thinker_profiles), Section 3.5 (thinker_metrics).
"""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

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
    primary_affiliation: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    twitter_handle: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    wikipedia_url: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    personal_site: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    approval_status: Mapped[str] = mapped_column(sa.Text, server_default="pending_llm")
    approved_backfill_days: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    approved_source_types: Mapped[list[str] | None] = mapped_column(
        ARRAY(sa.Text),
        nullable=True,
    )
    active: Mapped[bool] = mapped_column(sa.Boolean, server_default=sa.text("true"))
    added_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), server_default=sa.text("NOW()"))
    last_refreshed: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)

    # Relationships. passive_deletes=True tells SQLAlchemy to rely on the
    # DB-level ON DELETE CASCADE / SET NULL configured in migration 005
    # instead of trying to null out FKs itself (which would error since the
    # junction PKs include the FK columns).
    sources: Mapped[list["Source"]] = relationship(
        back_populates="thinker",
        lazy="selectin",
    )
    profiles: Mapped[list["ThinkerProfile"]] = relationship(
        back_populates="thinker",
        lazy="selectin",
        passive_deletes=True,
    )
    metrics: Mapped[list["ThinkerMetrics"]] = relationship(
        back_populates="thinker",
        lazy="selectin",
        passive_deletes=True,
    )
    categories: Mapped[list["ThinkerCategory"]] = relationship(
        lazy="selectin",
        passive_deletes=True,
    )

    def __repr__(self) -> str:
        return f"<Thinker(slug={self.slug!r}, tier={self.tier})>"


class ThinkerProfile(Base):
    """Extended profile data for a thinker (education, positions, works, awards)."""

    __tablename__ = "thinker_profiles"

    id: Mapped[uuid_pk]
    thinker_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("thinkers.id", ondelete="CASCADE"))
    # DATA-REVIEW L1: JSONB server default is '[]' (array), so the in-memory
    # shape is list[dict] not dict. Keeping Mapped[dict] on an array default
    # is a static-type lie that would break the first caller who trusted the
    # annotation to `.items()` an education row.
    education: Mapped[list] = mapped_column(JSONB, server_default=sa.text("'[]'::jsonb"))
    positions_held: Mapped[list] = mapped_column(JSONB, server_default=sa.text("'[]'::jsonb"))
    notable_works: Mapped[list] = mapped_column(JSONB, server_default=sa.text("'[]'::jsonb"))
    awards: Mapped[list] = mapped_column(JSONB, server_default=sa.text("'[]'::jsonb"))
    updated_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), server_default=sa.text("NOW()"))

    # Relationships
    thinker: Mapped["Thinker"] = relationship(back_populates="profiles")

    def __repr__(self) -> str:
        return f"<ThinkerProfile(thinker_id={self.thinker_id})>"


class ThinkerMetrics(Base):
    """Platform-specific metrics snapshot for a thinker."""

    __tablename__ = "thinker_metrics"

    # DATA-REVIEW L2: each (thinker, platform) pair may produce at most one
    # metrics snapshot per UTC day. The invariant is enforced by migration
    # 012 via a functional unique index; declaring the same index in the
    # model keeps Base.metadata.create_all (used by the test suite) in sync
    # with the alembic-managed schema so integration tests exercise the
    # same constraint production runs under.
    __table_args__ = (
        sa.Index(
            "ux_thinker_metrics_daily",
            "thinker_id",
            "platform",
            sa.text("((snapshotted_at AT TIME ZONE 'UTC')::date)"),
            unique=True,
        ),
    )

    id: Mapped[uuid_pk]
    thinker_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("thinkers.id", ondelete="CASCADE"))
    platform: Mapped[str] = mapped_column(sa.Text)
    handle: Mapped[str] = mapped_column(sa.Text)
    followers: Mapped[int] = mapped_column(sa.BigInteger)
    avg_views: Mapped[int] = mapped_column(sa.BigInteger)
    post_count: Mapped[int] = mapped_column(sa.Integer)
    verified: Mapped[bool] = mapped_column(sa.Boolean)
    snapshotted_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), server_default=sa.text("NOW()"))

    # Relationships
    thinker: Mapped["Thinker"] = relationship(back_populates="metrics")

    def __repr__(self) -> str:
        return f"<ThinkerMetrics(thinker_id={self.thinker_id}, platform={self.platform!r})>"
