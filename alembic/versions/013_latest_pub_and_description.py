"""Add sources.latest_published_at + content.description.

Revision ID: 013_latest_pub_and_description
Revises: 012_thinker_metrics_daily_unique
Create Date: 2026-04-17

Source: HANDLERS-REVIEW LO-05, ME-05.

LO-05: ``fetch_podcast_feed`` currently filters new entries with
``entry.published_at <= source.last_fetched``. ``last_fetched`` is the
wall-clock time we ran the fetch, not the latest publish timestamp we
already have on file -- so the invariant drifts whenever fetch timing
and publish timing don't line up (clock skew, paused/resumed workers,
feeds that publish slightly before we poll). Tracking the actual
maximum ``published_at`` we've seen on each source and comparing
against *that* is the stable invariant.

ME-05: ``rescan_cataloged_for_thinker`` only has access to
``Content.title`` for retroactive name matching. Feed entries carry a
description that often contains guest names the title doesn't (e.g.
"Lex Fridman Podcast #412" in the title, "Jensen Huang on the future
of GPUs" in the description). Persisting the description unlocks
retroactive matching without re-fetching feeds.

Both columns are additive and nullable. ``sources.latest_published_at``
is backfilled from ``MAX(content.published_at)`` per source so the
next incremental fetch has a correct high-water mark; ``content.description``
is left NULL for historical rows (feed_parser will populate going
forward).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "013_latest_pub_and_description"
down_revision: str | Sequence[str] | None = "012_thinker_metrics_daily_unique"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "sources",
        sa.Column("latest_published_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "content",
        sa.Column("description", sa.Text(), nullable=True),
    )

    # Backfill source.latest_published_at from existing content so the
    # first post-migration incremental fetch has an accurate high-water
    # mark. Without this, the skip condition would allow every historical
    # entry to be re-processed on the first run (or fall back to
    # last_fetched and keep the old drift bug).
    op.execute(
        """
        UPDATE sources s
        SET latest_published_at = sub.max_pub
        FROM (
            SELECT source_id, MAX(published_at) AS max_pub
            FROM content
            WHERE published_at IS NOT NULL
            GROUP BY source_id
        ) sub
        WHERE s.id = sub.source_id
        """
    )


def downgrade() -> None:
    op.drop_column("content", "description")
    op.drop_column("sources", "latest_published_at")
