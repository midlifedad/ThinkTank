"""Add b-tree index on content.url for dedup lookups.

Revision ID: 011_index_content_url
Revises: 010_content_source_guid
Create Date: 2026-04-17

Source: DATA-REVIEW M3.

The ingestion pipeline and admin/source-detail pages look up content by
``url`` frequently: the dedup path in ``refresh_due_sources``, the
per-source episode list, and ad-hoc Admin searches all filter
``WHERE url = :url`` (or ``url IN (...)`` for batch lookups). Postgres
has ``canonical_url UNIQUE`` which gives us an index there, but ``url``
is the pre-canonicalization string and is searched independently --
there was no index on it, so every lookup was a seq scan on the full
content table.

Non-unique because ``url`` legitimately repeats across feed mirrors
during ingestion (the canonicalizer runs after insert and only
``canonical_url`` is the dedup invariant).

As with 009_index_hot_fks, this is a locking ``CREATE INDEX`` -- fine
for the current corpus, needs a CONCURRENTLY follow-up once content
grows past a few million rows.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "011_index_content_url"
down_revision: str | Sequence[str] | None = "010_content_source_guid"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index("ix_content_url", "content", ["url"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_content_url", table_name="content")
