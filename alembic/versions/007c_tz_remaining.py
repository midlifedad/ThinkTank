"""Convert remaining small tables' timestamps to TIMESTAMPTZ (part 3 of 3).

Revision ID: 007c_tz_remaining
Revises: 007b_tz_jobs
Create Date: 2026-04-16

Split of the original 007 migration per Troy's deploy-ordering review.
See 007a for the motivation behind the three-way split.

Part 3 covers the small reference tables where ALTER COLUMN TYPE
lock duration is negligible:

* ``thinkers``          -- curator-managed, small row count
* ``thinker_profiles``  -- 1 row per thinker
* ``thinker_metrics``   -- monthly snapshots
* ``sources``           -- curator-managed, small row count
* ``categories``        -- small taxonomy
* ``system_config``     -- key/value singleton table

``USING ... AT TIME ZONE 'UTC'`` preserves the stored instant.
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "007c_tz_remaining"
down_revision: Union[str, Sequence[str], None] = "007b_tz_jobs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_TIMESTAMP_COLUMNS: tuple[tuple[str, str], ...] = (
    # thinkers + profile/metrics
    ("thinkers", "added_at"),
    ("thinkers", "last_refreshed"),
    ("thinker_profiles", "updated_at"),
    ("thinker_metrics", "snapshotted_at"),
    # sources
    ("sources", "created_at"),
    ("sources", "last_fetched"),
    # categories (TimestampMixin)
    ("categories", "created_at"),
    # system config
    ("system_config", "updated_at"),
)


def upgrade() -> None:
    for table, column in _TIMESTAMP_COLUMNS:
        op.execute(
            f'ALTER TABLE {table} '
            f'ALTER COLUMN {column} TYPE TIMESTAMP WITH TIME ZONE '
            f"USING {column} AT TIME ZONE 'UTC'"
        )


def downgrade() -> None:
    for table, column in reversed(_TIMESTAMP_COLUMNS):
        op.execute(
            f'ALTER TABLE {table} '
            f'ALTER COLUMN {column} TYPE TIMESTAMP WITHOUT TIME ZONE '
            f"USING {column} AT TIME ZONE 'UTC'"
        )
