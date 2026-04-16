"""Category and ThinkerCategory (junction) models.

Spec references: Section 3.1 (categories), Section 3.3 (thinker_categories).
"""

import uuid
from datetime import datetime
from typing import Optional

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from thinktank.models.base import Base, TimestampMixin, uuid_pk


class Category(TimestampMixin, Base):
    """Knowledge domain category with optional parent for hierarchical taxonomy."""

    __tablename__ = "categories"

    id: Mapped[uuid_pk]
    slug: Mapped[str] = mapped_column(sa.Text, unique=True)
    name: Mapped[str] = mapped_column(sa.Text)
    parent_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        sa.ForeignKey("categories.id"),
        nullable=True,
    )
    description: Mapped[str] = mapped_column(sa.Text)

    # Self-referential relationships
    parent: Mapped[Optional["Category"]] = relationship(
        back_populates="children",
        remote_side="Category.id",
        lazy="selectin",
    )
    children: Mapped[list["Category"]] = relationship(
        back_populates="parent",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<Category(slug={self.slug!r})>"


class ThinkerCategory(Base):
    """Junction table linking thinkers to categories with relevance score.

    Composite primary key on (thinker_id, category_id).
    """

    __tablename__ = "thinker_categories"

    thinker_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("thinkers.id", ondelete="CASCADE"),
        primary_key=True,
    )
    category_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("categories.id", ondelete="CASCADE"),
        primary_key=True,
    )
    relevance: Mapped[int] = mapped_column(sa.SmallInteger)
    added_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.text("NOW()")
    )

    # Relationship for eager-loading category names without N+1 queries.
    category: Mapped["Category"] = relationship()

    def __repr__(self) -> str:
        return f"<ThinkerCategory(thinker={self.thinker_id}, cat={self.category_id})>"


class SourceCategory(Base):
    """Junction table linking sources to categories with relevance score.

    Composite primary key on (source_id, category_id).
    """

    __tablename__ = "source_categories"

    source_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("sources.id", ondelete="CASCADE"),
        primary_key=True,
    )
    category_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("categories.id", ondelete="CASCADE"),
        primary_key=True,
    )
    relevance: Mapped[int] = mapped_column(sa.SmallInteger)
    added_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.text("NOW()")
    )

    def __repr__(self) -> str:
        return f"<SourceCategory(source={self.source_id}, cat={self.category_id})>"
