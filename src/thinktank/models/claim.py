"""Claims layer models: inquiries, canonical claims, observations, documents.

Claims v2 milestone (Amir design session 2026-07-13). Two-layer belief
database over the transcript corpus + web evidence:

    claim_observations  -- the EVIDENCE record: atomic, verbatim-grounded
                           instances of an expert asserting something, with
                           polymorphic provenance (our corpus OR a fetched
                           web document) and asserted_at (when the statement
                           was MADE, not found). Append-only.

    claims              -- the CANONICAL registry: stable, stance-neutral
                           propositions that observations resolve onto.
                           This is what gets tracked over time (stance
                           distributions, position changes, consensus).
                           Fine-grained claims link upward to a headline
                           inquiry claim via parent_claim_id.

    inquiries           -- Mode A (proactive): a question posed across the
                           vetted expert roster of an area. Each inquiry's
                           headline proposition is a canonical claim; each
                           expert gets a REQUIRED resolved position row.

    inquiry_positions   -- per (inquiry, thinker): the resolved stance +
                           position summary on the headline question,
                           synthesized from that expert's observations.
                           The stance-matrix row.

    documents           -- web provenance: fetched pages with stored text
                           (grounding stays verifiable if the page dies),
                           published_at vs retrieved_at kept distinct.

    content_chunks      -- speaker-turn chunks of transcripts with
                           embeddings; the corpus-lane retrieval index.

Embeddings are vector(768) (local bge-base-class model served by the Mac
inference service). A model switch means a new column + reindex; the
dimension is deliberately pinned at the schema level.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from thinktank.models.base import Base, uuid_pk
from thinktank.models.constants import (
    ALLOWED_CLAIM_TYPES,
    ALLOWED_INQUIRY_STATUSES,
    ALLOWED_OBSERVATION_ORIGINS,
    ALLOWED_STANCES,
)

EMBEDDING_DIM = 768


def _check(values: tuple[str, ...], column: str, name: str) -> sa.CheckConstraint:
    quoted = ", ".join(f"'{v}'" for v in values)
    return sa.CheckConstraint(f"{column} IN ({quoted})", name=name)


class Inquiry(Base):
    """A proactive question posed across an area's vetted expert roster."""

    __tablename__ = "inquiries"
    __table_args__ = (_check(ALLOWED_INQUIRY_STATUSES, "status", "ck_inquiry_status"),)

    id: Mapped[uuid_pk]
    question: Mapped[str] = mapped_column(sa.Text)
    area: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    canonical_claim_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("claims.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(sa.Text, server_default="pending")
    triggered_by: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), server_default=sa.text("NOW()"))
    completed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:
        return f"<Inquiry(question={self.question[:40]!r}, status={self.status!r})>"


class Claim(Base):
    """A canonical, stance-neutral proposition -- the tracked belief entity."""

    __tablename__ = "claims"
    __table_args__ = (_check(ALLOWED_CLAIM_TYPES, "claim_type", "ck_claim_type"),)

    id: Mapped[uuid_pk]
    proposition: Mapped[str] = mapped_column(sa.Text)
    claim_type: Mapped[str] = mapped_column(sa.Text)
    # Fine-grained claims link upward to the headline inquiry claim.
    parent_claim_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("claims.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(sa.Text, server_default="active")
    # Canonical-claim dedup discovered late: same survivor pattern as
    # content dedup (008).
    merged_into_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("claims.id", ondelete="SET NULL"), nullable=True
    )
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM), nullable=True)
    # Maintained by the resolution stage.
    first_observed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    last_observed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    observation_count: Mapped[int] = mapped_column(sa.Integer, server_default=sa.text("0"))
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), server_default=sa.text("NOW()"))

    def __repr__(self) -> str:
        return f"<Claim(proposition={self.proposition[:50]!r}, type={self.claim_type!r})>"


class ClaimCategory(Base):
    """Taxonomy junction: canonical claims classified into the categories tree."""

    __tablename__ = "claim_categories"

    claim_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("claims.id", ondelete="CASCADE"), primary_key=True)
    category_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("categories.id", ondelete="CASCADE"), primary_key=True)
    relevance: Mapped[int] = mapped_column(sa.SmallInteger, server_default=sa.text("5"))
    added_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), server_default=sa.text("NOW()"))


class Document(Base):
    """Web provenance: a fetched page whose text is stored for grounding."""

    __tablename__ = "documents"

    id: Mapped[uuid_pk]
    url: Mapped[str] = mapped_column(sa.Text, unique=True)
    domain: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    title: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    author: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    # When the content was PUBLISHED (claim date) vs when we FETCHED it.
    published_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    retrieved_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), server_default=sa.text("NOW()"))
    # Extracted text stored so grounding stays verifiable if the page dies.
    text_content: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    fetch_status: Mapped[str] = mapped_column(sa.Text, server_default="fetched")
    found_via: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    search_query: Mapped[str | None] = mapped_column(sa.Text, nullable=True)

    def __repr__(self) -> str:
        return f"<Document(url={self.url[:60]!r})>"


class ClaimObservation(Base):
    """One evidence-backed instance of an expert taking a position.

    Provenance is polymorphic but REQUIRED: exactly one of content_id
    (our transcript corpus) or document_id (fetched web page). An
    observation without provenance is not evidence and cannot exist.
    """

    __tablename__ = "claim_observations"
    __table_args__ = (
        _check(ALLOWED_CLAIM_TYPES, "claim_type", "ck_observation_claim_type"),
        _check(ALLOWED_STANCES, "stance", "ck_observation_stance"),
        _check(ALLOWED_OBSERVATION_ORIGINS, "origin", "ck_observation_origin"),
        sa.CheckConstraint(
            "(content_id IS NOT NULL)::int + (document_id IS NOT NULL)::int = 1",
            name="ck_observation_one_provenance",
        ),
    )

    id: Mapped[uuid_pk]
    # Null until the resolution stage attaches it to a canonical claim.
    claim_id: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("claims.id", ondelete="SET NULL"), nullable=True)
    inquiry_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("inquiries.id", ondelete="SET NULL"), nullable=True
    )
    thinker_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("thinkers.id", ondelete="SET NULL"), nullable=True
    )
    speaker_label: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    origin: Mapped[str] = mapped_column(sa.Text)
    claim_type: Mapped[str] = mapped_column(sa.Text)
    stance: Mapped[str] = mapped_column(sa.Text)
    claim_text: Mapped[str] = mapped_column(sa.Text)
    # Hedging: asserted | speculated | reported (spoken confidence, not ours)
    confidence: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    quote: Mapped[str] = mapped_column(sa.Text)
    quote_start: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    quote_end: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    # True when the quote was programmatically located in the provenance
    # text (hard grounding for corpus, soft for web).
    grounded: Mapped[bool] = mapped_column(sa.Boolean, server_default=sa.text("false"))
    content_id: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("content.id", ondelete="CASCADE"), nullable=True)
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=True
    )
    # When the statement was MADE (episode published_at / doc published_at).
    asserted_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    topics: Mapped[list[str] | None] = mapped_column(ARRAY(sa.Text), nullable=True)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM), nullable=True)
    extraction_model: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    extracted_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), server_default=sa.text("NOW()"))

    def __repr__(self) -> str:
        return f"<ClaimObservation(stance={self.stance!r}, text={self.claim_text[:40]!r})>"


class InquiryPosition(Base):
    """Per (inquiry, thinker): the REQUIRED resolved position on the
    headline question, synthesized from that expert's observations.

    The stance-matrix row (Amir's hybrid grain: fine-grained claims must
    still resolve to an answer on the main inquiry).
    """

    __tablename__ = "inquiry_positions"
    __table_args__ = (_check(ALLOWED_STANCES + ("unknown",), "stance", "ck_position_stance"),)

    inquiry_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("inquiries.id", ondelete="CASCADE"), primary_key=True)
    thinker_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("thinkers.id", ondelete="CASCADE"), primary_key=True)
    stance: Mapped[str] = mapped_column(sa.Text)
    position_summary: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    observation_count: Mapped[int] = mapped_column(sa.Integer, server_default=sa.text("0"))
    resolution_model: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    resolved_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), server_default=sa.text("NOW()"))
    details: Mapped[dict | None] = mapped_column(JSONB, nullable=True)


class ContentChunk(Base):
    """Speaker-turn chunk of a transcript with its embedding (corpus lane)."""

    __tablename__ = "content_chunks"
    __table_args__ = (sa.UniqueConstraint("content_id", "chunk_index", name="uq_chunk_content_index"),)

    id: Mapped[uuid_pk]
    content_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("content.id", ondelete="CASCADE"))
    chunk_index: Mapped[int] = mapped_column(sa.Integer)
    speaker_label: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    text: Mapped[str] = mapped_column(sa.Text)
    char_start: Mapped[int] = mapped_column(sa.Integer)
    char_end: Mapped[int] = mapped_column(sa.Integer)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM), nullable=True)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), server_default=sa.text("NOW()"))
