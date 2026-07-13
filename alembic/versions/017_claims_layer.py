"""Claims layer: pgvector + inquiries, canonical claims, observations, documents.

Revision ID: 017_claims_layer
Revises: 016_candidate_vetting
Create Date: 2026-07-13

Claims v2 milestone (Amir design session 2026-07-13): the two-layer
belief database. See models/claim.py for the full design narrative.

DEPLOY PREREQUISITE: the production Postgres must have the pgvector
extension available (Railway image swap owned by deploy-ops -- do NOT
merge/deploy this migration before that lands). Local dev + CI + tests
run the pgvector/pgvector:pg16 image.

Embedding dimension is pinned at 768 (local bge-base-class model on the
Mac inference service). HNSW indexes are created up front -- cheap on
empty tables, and inserts maintain them incrementally.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import ARRAY, JSONB

# revision identifiers, used by Alembic.
revision: str = "017_claims_layer"
down_revision: str | Sequence[str] | None = "016_candidate_vetting"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EMBEDDING_DIM = 768

_CLAIM_TYPES = "'factual', 'prediction', 'opinion', 'practice', 'recommendation'"
_STANCES = "'asserts', 'denies', 'hedges', 'questions', 'reports'"


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "claims",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("proposition", sa.Text(), nullable=False),
        sa.Column("claim_type", sa.Text(), nullable=False),
        sa.Column("parent_claim_id", sa.UUID(), sa.ForeignKey("claims.id", ondelete="SET NULL"), nullable=True),
        sa.Column("status", sa.Text(), server_default="active", nullable=False),
        sa.Column("merged_into_id", sa.UUID(), sa.ForeignKey("claims.id", ondelete="SET NULL"), nullable=True),
        sa.Column("embedding", Vector(EMBEDDING_DIM), nullable=True),
        sa.Column("first_observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("observation_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.CheckConstraint(f"claim_type IN ({_CLAIM_TYPES})", name="ck_claim_type"),
    )
    op.create_index("ix_claims_parent", "claims", ["parent_claim_id"])

    op.create_table(
        "inquiries",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("area", sa.Text(), nullable=True),
        sa.Column("canonical_claim_id", sa.UUID(), sa.ForeignKey("claims.id", ondelete="SET NULL"), nullable=True),
        sa.Column("status", sa.Text(), server_default="pending", nullable=False),
        sa.Column("triggered_by", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('pending', 'running', 'complete', 'failed')", name="ck_inquiry_status"),
    )

    op.create_table(
        "claim_categories",
        sa.Column("claim_id", sa.UUID(), sa.ForeignKey("claims.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("category_id", sa.UUID(), sa.ForeignKey("categories.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("relevance", sa.SmallInteger(), server_default=sa.text("5"), nullable=False),
        sa.Column("added_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
    )

    op.create_table(
        "documents",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("url", sa.Text(), nullable=False, unique=True),
        sa.Column("domain", sa.Text(), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("author", sa.Text(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("text_content", sa.Text(), nullable=True),
        sa.Column("fetch_status", sa.Text(), server_default="fetched", nullable=False),
        sa.Column("found_via", sa.Text(), nullable=True),
        sa.Column("search_query", sa.Text(), nullable=True),
    )

    op.create_table(
        "claim_observations",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("claim_id", sa.UUID(), sa.ForeignKey("claims.id", ondelete="SET NULL"), nullable=True),
        sa.Column("inquiry_id", sa.UUID(), sa.ForeignKey("inquiries.id", ondelete="SET NULL"), nullable=True),
        sa.Column("thinker_id", sa.UUID(), sa.ForeignKey("thinkers.id", ondelete="SET NULL"), nullable=True),
        sa.Column("speaker_label", sa.Text(), nullable=True),
        sa.Column("origin", sa.Text(), nullable=False),
        sa.Column("claim_type", sa.Text(), nullable=False),
        sa.Column("stance", sa.Text(), nullable=False),
        sa.Column("claim_text", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Text(), nullable=True),
        sa.Column("quote", sa.Text(), nullable=False),
        sa.Column("quote_start", sa.Integer(), nullable=True),
        sa.Column("quote_end", sa.Integer(), nullable=True),
        sa.Column("grounded", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("content_id", sa.UUID(), sa.ForeignKey("content.id", ondelete="CASCADE"), nullable=True),
        sa.Column("document_id", sa.UUID(), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=True),
        sa.Column("asserted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("topics", ARRAY(sa.Text()), nullable=True),
        sa.Column("embedding", Vector(EMBEDDING_DIM), nullable=True),
        sa.Column("extraction_model", sa.Text(), nullable=True),
        sa.Column("extracted_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.CheckConstraint(f"claim_type IN ({_CLAIM_TYPES})", name="ck_observation_claim_type"),
        sa.CheckConstraint(f"stance IN ({_STANCES})", name="ck_observation_stance"),
        sa.CheckConstraint("origin IN ('inquiry', 'autonomous')", name="ck_observation_origin"),
        sa.CheckConstraint(
            "(content_id IS NOT NULL)::int + (document_id IS NOT NULL)::int = 1",
            name="ck_observation_one_provenance",
        ),
    )
    op.create_index("ix_observations_claim", "claim_observations", ["claim_id"])
    op.create_index("ix_observations_thinker", "claim_observations", ["thinker_id"])
    op.create_index("ix_observations_inquiry", "claim_observations", ["inquiry_id"])
    op.create_index("ix_observations_content", "claim_observations", ["content_id"])
    op.create_index("ix_observations_asserted", "claim_observations", ["asserted_at"])

    op.create_table(
        "inquiry_positions",
        sa.Column("inquiry_id", sa.UUID(), sa.ForeignKey("inquiries.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("thinker_id", sa.UUID(), sa.ForeignKey("thinkers.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("stance", sa.Text(), nullable=False),
        sa.Column("position_summary", sa.Text(), nullable=True),
        sa.Column("observation_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("resolution_model", sa.Text(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("details", JSONB(), nullable=True),
        sa.CheckConstraint(f"stance IN ({_STANCES}, 'unknown')", name="ck_position_stance"),
    )

    op.create_table(
        "content_chunks",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("content_id", sa.UUID(), sa.ForeignKey("content.id", ondelete="CASCADE"), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("speaker_label", sa.Text(), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("char_start", sa.Integer(), nullable=False),
        sa.Column("char_end", sa.Integer(), nullable=False),
        sa.Column("embedding", Vector(EMBEDDING_DIM), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.UniqueConstraint("content_id", "chunk_index", name="uq_chunk_content_index"),
    )
    op.create_index("ix_chunks_content", "content_chunks", ["content_id"])

    # HNSW ANN indexes (cosine). Cheap on empty tables; maintained on insert.
    op.execute("CREATE INDEX ix_claims_embedding ON claims USING hnsw (embedding vector_cosine_ops)")
    op.execute("CREATE INDEX ix_observations_embedding ON claim_observations USING hnsw (embedding vector_cosine_ops)")
    op.execute("CREATE INDEX ix_chunks_embedding ON content_chunks USING hnsw (embedding vector_cosine_ops)")


def downgrade() -> None:
    op.drop_table("content_chunks")
    op.drop_table("inquiry_positions")
    op.drop_table("claim_observations")
    op.drop_table("documents")
    op.drop_table("claim_categories")
    op.drop_table("inquiries")
    op.drop_table("claims")
    # Extension left in place: other consumers may exist and dropping it
    # is an operator decision, not a migration side effect.
