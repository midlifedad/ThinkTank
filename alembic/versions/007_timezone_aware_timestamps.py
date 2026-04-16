"""Convert every timestamp column to TIMESTAMPTZ.

Revision ID: 007_timezone_aware
Revises: 006_status_check
Create Date: 2026-04-16

Source: DATA-REVIEW H4 / HANDLERS-REVIEW LO-06 / INTEGRATIONS-REVIEW H-03.

Every existing ``TIMESTAMP WITHOUT TIME ZONE`` column is converted to
``TIMESTAMP WITH TIME ZONE``. The ``USING ... AT TIME ZONE 'UTC'`` clause
preserves the stored instant by declaring the incoming naive values were
already UTC (which is what every production writer intended via
``datetime.now(UTC).replace(tzinfo=None)``).

After this migration Python callers must pass timezone-aware datetimes
(``datetime.now(UTC)``); factories, handlers, admin routers, and feed
parsing have been updated to match.
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "007_timezone_aware"
down_revision: Union[str, Sequence[str], None] = "006_status_check"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# (table, column) pairs. Covers every TIMESTAMP column defined across the
# SQLAlchemy models (grepped via information_schema before migration).
_TIMESTAMP_COLUMNS: tuple[tuple[str, str], ...] = (
    # thinkers + profile/metrics
    ("thinkers", "added_at"),
    ("thinkers", "last_refreshed"),
    ("thinker_profiles", "updated_at"),
    ("thinker_metrics", "snapshotted_at"),
    ("thinker_categories", "added_at"),
    # sources + junction
    ("sources", "created_at"),
    ("sources", "last_fetched"),
    ("source_thinkers", "added_at"),
    ("source_categories", "added_at"),
    # categories (TimestampMixin)
    ("categories", "created_at"),
    # content + junction
    ("content", "published_at"),
    ("content", "discovered_at"),
    ("content", "processed_at"),
    ("content_thinkers", "added_at"),
    # candidates
    ("candidate_thinkers", "first_seen_at"),
    ("candidate_thinkers", "last_seen_at"),
    ("candidate_thinkers", "reviewed_at"),
    # jobs
    ("jobs", "last_error_at"),
    ("jobs", "scheduled_at"),
    ("jobs", "started_at"),
    ("jobs", "completed_at"),
    ("jobs", "created_at"),
    # llm reviews
    ("llm_reviews", "overridden_at"),
    ("llm_reviews", "created_at"),
    # misc
    ("system_config", "updated_at"),
    ("rate_limit_usage", "called_at"),
    ("api_usage", "period_start"),
)


def upgrade() -> None:
    """Convert every timestamp column to TIMESTAMPTZ, treating stored
    naive values as UTC."""
    for table, column in _TIMESTAMP_COLUMNS:
        op.execute(
            f'ALTER TABLE {table} '
            f'ALTER COLUMN {column} TYPE TIMESTAMP WITH TIME ZONE '
            f"USING {column} AT TIME ZONE 'UTC'"
        )


def downgrade() -> None:
    """Revert every timestamp column back to TIMESTAMP WITHOUT TIME ZONE.

    The ``AT TIME ZONE 'UTC'`` clause strips the offset, preserving the
    UTC wall-clock value as a naive datetime.
    """
    for table, column in reversed(_TIMESTAMP_COLUMNS):
        op.execute(
            f'ALTER TABLE {table} '
            f'ALTER COLUMN {column} TYPE TIMESTAMP WITHOUT TIME ZONE '
            f"USING {column} AT TIME ZONE 'UTC'"
        )
