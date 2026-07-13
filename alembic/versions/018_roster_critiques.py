"""Roster critiques: LLM comparative review of a vetted area slate.

Revision ID: 018_roster_critiques
Revises: 017_claims_layer
Create Date: 2026-07-13

Dynamic Expert Standing Phase 1b (docs/plans/
2026-07-13-dynamic-expert-standing.md): per-candidate vetting cannot see
relative misranking or absent names -- only a roster-level view can.
Each critique row stores the LLM's {misranked, missing} verdict for one
area; missing names are inserted as candidates and vetted normally.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = "018_roster_critiques"
down_revision: str | Sequence[str] | None = "017_claims_layer"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "roster_critiques",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("search_area", sa.Text(), nullable=False),
        sa.Column("critique", JSONB(), nullable=False),
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column("candidates_reviewed", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("nominated", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
    )
    op.create_index("ix_roster_critiques_area", "roster_critiques", ["search_area", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_roster_critiques_area", table_name="roster_critiques")
    op.drop_table("roster_critiques")
