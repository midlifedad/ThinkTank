"""Convert timestamps on queue + usage-tracking tables to TIMESTAMPTZ (part 2 of 3).

Revision ID: 007b_tz_jobs
Revises: 007a_tz_content
Create Date: 2026-04-16

Split of the original 007 migration per Troy's deploy-ordering review.
See 007a for the motivation behind the three-way split.

Part 2 covers the queue + usage-tracking tables. These are
mid-throughput: the queue sees constant writes from the job workers
but is typically smaller than ``content``; ``api_usage`` and
``rate_limit_usage`` are append-only but lightweight:

* ``jobs``                -- 5 columns, queue pipeline
* ``api_usage``           -- append-only
* ``rate_limit_usage``    -- append-only

``USING ... AT TIME ZONE 'UTC'`` preserves the stored instant.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "007b_tz_jobs"
down_revision: str | Sequence[str] | None = "007a_tz_content"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_TIMESTAMP_COLUMNS: tuple[tuple[str, str], ...] = (
    # jobs
    ("jobs", "last_error_at"),
    ("jobs", "scheduled_at"),
    ("jobs", "started_at"),
    ("jobs", "completed_at"),
    ("jobs", "created_at"),
    # usage tracking
    ("rate_limit_usage", "called_at"),
    ("api_usage", "period_start"),
)


def upgrade() -> None:
    for table, column in _TIMESTAMP_COLUMNS:
        op.execute(
            f"ALTER TABLE {table} ALTER COLUMN {column} TYPE TIMESTAMP WITH TIME ZONE USING {column} AT TIME ZONE 'UTC'"
        )


def downgrade() -> None:
    for table, column in reversed(_TIMESTAMP_COLUMNS):
        op.execute(
            f"ALTER TABLE {table} "
            f"ALTER COLUMN {column} TYPE TIMESTAMP WITHOUT TIME ZONE "
            f"USING {column} AT TIME ZONE 'UTC'"
        )
