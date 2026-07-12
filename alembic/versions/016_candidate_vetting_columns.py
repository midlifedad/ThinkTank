"""Candidate vetting columns: evidence dossier, qualification score, seed provenance.

Revision ID: 016_candidate_vetting
Revises: 015_llm_token_split
Create Date: 2026-07-12

Expert Discovery & Vetting pipeline (Amir spec 2026-07-12): candidates are
surfaced (Perplexity deep research / OpenAlex / metadata mining), then
VETTED against structured evidence APIs into a JSONB dossier and scored by
a deterministic rubric. Only shortlisted candidates reach the LLM judge.

- evidence: raw per-source evidence dossier (openalex/wikidata/books/
  youtube/podcastindex/substack blocks + seed citations)
- qualification_score / score_breakdown: rubric output (transparent bands)
- search_area: the area/category query that surfaced this candidate
- seed_source: perplexity | openalex | metadata | manual
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = "016_candidate_vetting"
down_revision: str | Sequence[str] | None = "015_llm_token_split"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("candidate_thinkers", sa.Column("evidence", JSONB(), nullable=True))
    op.add_column("candidate_thinkers", sa.Column("qualification_score", sa.Integer(), nullable=True))
    op.add_column("candidate_thinkers", sa.Column("score_breakdown", JSONB(), nullable=True))
    op.add_column("candidate_thinkers", sa.Column("search_area", sa.Text(), nullable=True))
    op.add_column("candidate_thinkers", sa.Column("seed_source", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("candidate_thinkers", "seed_source")
    op.drop_column("candidate_thinkers", "search_area")
    op.drop_column("candidate_thinkers", "score_breakdown")
    op.drop_column("candidate_thinkers", "qualification_score")
    op.drop_column("candidate_thinkers", "evidence")
