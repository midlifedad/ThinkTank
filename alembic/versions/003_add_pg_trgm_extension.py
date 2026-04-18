"""add pg_trgm extension

Revision ID: 003_add_pg_trgm
Revises: 002_partial_claim
Create Date: 2026-03-09 02:40:00.000000

Enables pg_trgm extension for trigram similarity queries and creates a GiST
index on candidate_thinkers.normalized_name for efficient fuzzy name matching
at the 0.7 similarity threshold (spec Section 5.5, DISC-04).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "003_add_pg_trgm"
down_revision: str | Sequence[str] | None = "002_partial_claim"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Enable pg_trgm extension and create GiST index for trigram similarity."""
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.create_index(
        "ix_candidate_thinkers_trgm",
        "candidate_thinkers",
        [sa.text("normalized_name gist_trgm_ops")],
        postgresql_using="gist",
    )


def downgrade() -> None:
    """Remove GiST index and drop pg_trgm extension."""
    op.drop_index("ix_candidate_thinkers_trgm", table_name="candidate_thinkers")
    op.execute("DROP EXTENSION IF EXISTS pg_trgm")
