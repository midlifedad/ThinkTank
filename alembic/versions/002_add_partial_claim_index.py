"""add partial claim index

Revision ID: 002_partial_claim
Revises: 92ce969b2ede
Create Date: 2026-03-09 01:50:00.000000

Adds a partial index on jobs(priority, scheduled_at) WHERE status IN ('pending', 'retrying')
for the hot claim path. Keeps the existing ix_jobs_claim index for general status queries.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "002_partial_claim"
down_revision: str | Sequence[str] | None = "92ce969b2ede"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add partial index for claim query hot path."""
    op.execute(
        "CREATE INDEX ix_jobs_claimable ON jobs (priority, scheduled_at) WHERE status IN ('pending', 'retrying')"
    )


def downgrade() -> None:
    """Remove partial claim index."""
    op.drop_index("ix_jobs_claimable", table_name="jobs")
