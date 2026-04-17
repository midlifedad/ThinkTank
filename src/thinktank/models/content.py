"""Content and ContentThinker (junction) models.

Spec references: Section 3.7 (content), Section 3.8 (content_thinkers).
"""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from thinktank.models.base import Base, uuid_pk
from thinktank.models.constants import ALLOWED_CONTENT_STATUSES

if TYPE_CHECKING:
    from thinktank.models.source import Source
    from thinktank.models.thinker import Thinker


def _content_status_check() -> sa.CheckConstraint:
    """CHECK constraint for Content.status (DATA-REVIEW H3)."""
    values = ", ".join(f"'{s}'" for s in ALLOWED_CONTENT_STATUSES)
    return sa.CheckConstraint(
        f"status IN ({values})",
        name="ck_content_status",
    )


class Content(Base):
    """A piece of ingested content (episode, video, article, paper, post)."""

    __tablename__ = "content"
    __table_args__ = (_content_status_check(),)

    id: Mapped[uuid_pk]
    source_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("sources.id"))
    source_owner_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("thinkers.id"), nullable=True
    )  # DEPRECATED — use content_thinkers junction
    content_type: Mapped[str] = mapped_column(sa.Text)
    url: Mapped[str] = mapped_column(sa.Text, index=True)
    canonical_url: Mapped[str] = mapped_column(sa.Text, unique=True)
    content_fingerprint: Mapped[str | None] = mapped_column(
        sa.Text,
        unique=True,
        nullable=True,
    )
    source_guid: Mapped[str | None] = mapped_column(
        sa.Text,
        nullable=True,
    )
    title: Mapped[str] = mapped_column(sa.Text)
    description: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    body_text: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    word_count: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    duration_seconds: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    show_name: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    host_name: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    thumbnail_url: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    transcription_method: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    status: Mapped[str] = mapped_column(sa.Text, server_default="pending")
    error_message: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    discovered_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), server_default=sa.text("NOW()"))
    processed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)

    # Relationships
    source: Mapped["Source"] = relationship(back_populates="content")
    source_owner: Mapped[Optional["Thinker"]] = relationship()  # DEPRECATED
    content_thinkers: Mapped[list["ContentThinker"]] = relationship(
        back_populates="content",
        lazy="selectin",
        passive_deletes=True,
    )

    def __repr__(self) -> str:
        return f"<Content(title={self.title!r}, status={self.status!r})>"


class ContentThinker(Base):
    """Junction table linking content to thinkers with role attribution.

    Composite primary key on (content_id, thinker_id).
    """

    __tablename__ = "content_thinkers"

    content_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("content.id", ondelete="CASCADE"),
        primary_key=True,
    )
    thinker_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("thinkers.id", ondelete="CASCADE"),
        primary_key=True,
    )
    role: Mapped[str] = mapped_column(sa.Text)
    confidence: Mapped[int] = mapped_column(sa.SmallInteger)
    added_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), server_default=sa.text("NOW()"))

    # Relationships
    content: Mapped["Content"] = relationship(back_populates="content_thinkers")
    thinker: Mapped["Thinker"] = relationship()

    def __repr__(self) -> str:
        return f"<ContentThinker(content={self.content_id}, thinker={self.thinker_id}, role={self.role!r})>"
