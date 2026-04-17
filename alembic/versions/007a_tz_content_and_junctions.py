"""Convert timestamps on write-heavy tables to TIMESTAMPTZ (part 1 of 3).

Revision ID: 007a_tz_content
Revises: 009_index_hot_fks
Create Date: 2026-04-16

Split of the original 007 migration per Troy's deploy-ordering review.
ALTER COLUMN TYPE takes AccessExclusiveLock, which blocks all reads and
writes on the target table for the duration of the rewrite. Splitting
the 27-column batch into three migrations gives the deploy a natural
checkpoint between each group so a partial-outage window can be
bounded.

Part 1 covers the write-heavy / high-traffic tables:

* ``content``               -- 3 columns, largest table in the DB
* ``content_thinkers``      -- junction, hot reverse-lookup
* ``source_thinkers``       -- junction
* ``source_categories``     -- junction
* ``thinker_categories``    -- junction
* ``candidate_thinkers``    -- 3 columns, grows with discovery pipeline
* ``llm_reviews``           -- grows with every LLM decision

The ``USING ... AT TIME ZONE 'UTC'`` clause preserves the stored
instant by declaring the incoming naive values were already UTC
(which every production writer intended via
``datetime.now(UTC).replace(tzinfo=None)``).

After this migration (plus 007b/007c) Python callers must pass
timezone-aware datetimes (``datetime.now(UTC)``); factories,
handlers, admin routers, and feed parsing have been updated to match.
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "007a_tz_content"
down_revision: Union[str, Sequence[str], None] = "009_index_hot_fks"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_TIMESTAMP_COLUMNS: tuple[tuple[str, str], ...] = (
    # content + its junctions
    ("content", "published_at"),
    ("content", "discovered_at"),
    ("content", "processed_at"),
    ("content_thinkers", "added_at"),
    ("source_thinkers", "added_at"),
    ("source_categories", "added_at"),
    ("thinker_categories", "added_at"),
    # candidates
    ("candidate_thinkers", "first_seen_at"),
    ("candidate_thinkers", "last_seen_at"),
    ("candidate_thinkers", "reviewed_at"),
    # llm reviews
    ("llm_reviews", "overridden_at"),
    ("llm_reviews", "created_at"),
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
