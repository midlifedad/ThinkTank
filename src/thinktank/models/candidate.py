"""CandidateThinker model.

Spec reference: Section 3.9 (candidate_thinkers).
"""

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from thinktank.models.base import Base, uuid_pk


class CandidateThinker(Base):
    """A potential thinker surfaced by cascade discovery, pending LLM review."""

    __tablename__ = "candidate_thinkers"

    id: Mapped[uuid_pk]
    name: Mapped[str] = mapped_column(sa.Text)
    normalized_name: Mapped[str] = mapped_column(sa.Text)
    appearance_count: Mapped[int] = mapped_column(sa.Integer, server_default=sa.text("1"))
    first_seen_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), server_default=sa.text("NOW()"))
    last_seen_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), server_default=sa.text("NOW()"))
    sample_urls: Mapped[list[str] | None] = mapped_column(
        ARRAY(sa.Text),
        nullable=True,
    )
    inferred_categories: Mapped[list[str] | None] = mapped_column(
        ARRAY(sa.Text),
        nullable=True,
    )
    suggested_twitter: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    suggested_youtube: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    status: Mapped[str] = mapped_column(sa.Text, server_default="pending_llm")
    # Expert vetting (migration 016): structured evidence dossier gathered
    # from OpenAlex/Wikidata/books/YouTube/PodcastIndex/Substack, the
    # deterministic rubric's output, and seed provenance. Statuses used by
    # the vetting flow: vetting -> shortlisted | auto_rejected.
    evidence: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    qualification_score: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    score_breakdown: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    search_area: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    seed_source: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    llm_review_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("llm_reviews.id"),
        nullable=True,
    )
    reviewed_by: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    thinker_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("thinkers.id", ondelete="SET NULL"),
        nullable=True,
    )

    def __repr__(self) -> str:
        return f"<CandidateThinker(name={self.name!r}, status={self.status!r})>"
