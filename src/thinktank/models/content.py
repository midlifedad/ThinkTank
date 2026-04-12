"""Content and ContentThinker (junction) models.

Spec references: Section 3.7 (content), Section 3.8 (content_thinkers).
"""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.thinktank.models.base import Base, uuid_pk

if TYPE_CHECKING:
    from src.thinktank.models.source import Source
    from src.thinktank.models.thinker import Thinker


class Content(Base):
    """A piece of ingested content (episode, video, article, paper, post)."""

    __tablename__ = "content"

    id: Mapped[uuid_pk]
    source_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("sources.id"))
    source_owner_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        sa.ForeignKey("thinkers.id"), nullable=True
    )  # DEPRECATED — use content_thinkers junction
    content_type: Mapped[str] = mapped_column(sa.Text)
    url: Mapped[str] = mapped_column(sa.Text)
    canonical_url: Mapped[str] = mapped_column(sa.Text, unique=True)
    content_fingerprint: Mapped[Optional[str]] = mapped_column(
        sa.Text,
        unique=True,
        nullable=True,
    )
    title: Mapped[str] = mapped_column(sa.Text)
    body_text: Mapped[Optional[str]] = mapped_column(sa.Text, nullable=True)
    word_count: Mapped[Optional[int]] = mapped_column(sa.Integer, nullable=True)
    published_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    duration_seconds: Mapped[Optional[int]] = mapped_column(sa.Integer, nullable=True)
    show_name: Mapped[Optional[str]] = mapped_column(sa.Text, nullable=True)
    host_name: Mapped[Optional[str]] = mapped_column(sa.Text, nullable=True)
    thumbnail_url: Mapped[Optional[str]] = mapped_column(sa.Text, nullable=True)
    transcription_method: Mapped[Optional[str]] = mapped_column(sa.Text, nullable=True)
    status: Mapped[str] = mapped_column(sa.Text, server_default="pending")
    error_message: Mapped[Optional[str]] = mapped_column(sa.Text, nullable=True)
    discovered_at: Mapped[datetime] = mapped_column(server_default=sa.text("NOW()"))
    processed_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)

    # Relationships
    source: Mapped["Source"] = relationship(back_populates="content")
    source_owner: Mapped[Optional["Thinker"]] = relationship()  # DEPRECATED
    content_thinkers: Mapped[list["ContentThinker"]] = relationship(
        back_populates="content",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<Content(title={self.title!r}, status={self.status!r})>"


class ContentThinker(Base):
    """Junction table linking content to thinkers with role attribution.

    Composite primary key on (content_id, thinker_id).
    """

    __tablename__ = "content_thinkers"

    content_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("content.id"),
        primary_key=True,
    )
    thinker_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("thinkers.id"),
        primary_key=True,
    )
    role: Mapped[str] = mapped_column(sa.Text)
    confidence: Mapped[int] = mapped_column(sa.SmallInteger)
    added_at: Mapped[datetime] = mapped_column(server_default=sa.text("NOW()"))

    # Relationships
    content: Mapped["Content"] = relationship(back_populates="content_thinkers")
    thinker: Mapped["Thinker"] = relationship()

    def __repr__(self) -> str:
        return f"<ContentThinker(content={self.content_id}, thinker={self.thinker_id}, role={self.role!r})>"
