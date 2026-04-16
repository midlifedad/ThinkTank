"""CandidateThinker model.

Spec reference: Section 3.9 (candidate_thinkers).
"""

import uuid
from datetime import datetime
from typing import Optional

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from thinktank.models.base import Base, uuid_pk


class CandidateThinker(Base):
    """A potential thinker surfaced by cascade discovery, pending LLM review."""

    __tablename__ = "candidate_thinkers"

    id: Mapped[uuid_pk]
    name: Mapped[str] = mapped_column(sa.Text)
    normalized_name: Mapped[str] = mapped_column(sa.Text)
    appearance_count: Mapped[int] = mapped_column(sa.Integer, server_default=sa.text("1"))
    first_seen_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.text("NOW()")
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.text("NOW()")
    )
    sample_urls: Mapped[Optional[list[str]]] = mapped_column(
        ARRAY(sa.Text),
        nullable=True,
    )
    inferred_categories: Mapped[Optional[list[str]]] = mapped_column(
        ARRAY(sa.Text),
        nullable=True,
    )
    suggested_twitter: Mapped[Optional[str]] = mapped_column(sa.Text, nullable=True)
    suggested_youtube: Mapped[Optional[str]] = mapped_column(sa.Text, nullable=True)
    status: Mapped[str] = mapped_column(sa.Text, server_default="pending_llm")
    llm_review_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        sa.ForeignKey("llm_reviews.id"),
        nullable=True,
    )
    reviewed_by: Mapped[Optional[str]] = mapped_column(sa.Text, nullable=True)
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    thinker_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        sa.ForeignKey("thinkers.id"),
        nullable=True,
    )

    def __repr__(self) -> str:
        return f"<CandidateThinker(name={self.name!r}, status={self.status!r})>"
