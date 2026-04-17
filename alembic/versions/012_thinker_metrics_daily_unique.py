"""Partial unique index on thinker_metrics (thinker_id, platform, day).

Revision ID: 012_thinker_metrics_daily_unique
Revises: 011_index_content_url
Create Date: 2026-04-17

Source: DATA-REVIEW L2.

Each (thinker, platform) pair should produce at most one metrics snapshot
per day. Without a uniqueness invariant the rollup handler can insert
duplicate rows if retried mid-transaction or if two workers race on the
same thinker -- the ``thinker_detail`` page then shows two followers
counts for the same day and the sparkline double-counts.

Using a functional index on ``(snapshotted_at AT TIME ZONE 'UTC')::date``
rather than a composite unique constraint on a generated column keeps
the schema flat and lets Postgres handle the truncation on insert.

Postgres rejects ``date_trunc('day', snapshotted_at)`` on a TIMESTAMPTZ
column in a UNIQUE INDEX because that overload is marked STABLE (not
IMMUTABLE) -- its result depends on the session ``timezone`` GUC, so the
same row could hash to different index buckets across sessions. Casting
``AT TIME ZONE 'UTC'`` produces a naive TIMESTAMP, and the resulting
``::date`` call is IMMUTABLE. Pairing this with the session-level
``timezone=UTC`` forced in ``database.py`` means both readers and
writers see the same day boundary as the index does.

The table is empty on ``main`` (no rollup writer has shipped) so the
CREATE UNIQUE INDEX cannot conflict with existing rows. If data appears
before this migration runs in staging/prod, a pre-flight dedup would be
required -- noted here for future reference.

As with 009 / 011, this is a locking CREATE INDEX. Fine while the table
is empty; flag for CONCURRENTLY follow-up once metrics accumulate.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "012_thinker_metrics_daily_unique"
down_revision: str | Sequence[str] | None = "011_index_content_url"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE UNIQUE INDEX ux_thinker_metrics_daily
        ON thinker_metrics (
            thinker_id,
            platform,
            ((snapshotted_at AT TIME ZONE 'UTC')::date)
        )
        """
    )


def downgrade() -> None:
    op.drop_index("ux_thinker_metrics_daily", table_name="thinker_metrics")
