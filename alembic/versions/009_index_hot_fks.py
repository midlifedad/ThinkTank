"""Add covering b-tree indexes on hot foreign-key columns.

Revision ID: 009_index_hot_fks
Revises: 008_renormalize_urls
Create Date: 2026-04-16

Source: DATA-REVIEW M-level finding. Postgres does NOT auto-create an index
on the referencing side of a foreign key, so every JOIN / DELETE-cascade
against these columns was doing a sequential scan. The initial schema only
indexed primary keys, the trigram GIN on thinkers.name (003), the jobs
claim composite (92ce96.../002), and a handful of timeseries.

Indexes added (all plain b-tree, nullable-safe):

* ``ix_content_source_id``           -- content -> sources (hot; fetch
  handlers paginate content by source).
* ``ix_content_source_owner_id``     -- content -> thinkers (legacy; still
  populated during the sources-first-class transition, deprecated but not
  removed).
* ``ix_sources_thinker_id``          -- sources -> thinkers (legacy,
  nullable after migration 004; kept for completeness).
* ``ix_source_thinkers_thinker_id``  -- junction reverse lookup (find all
  sources for a thinker). The PK already covers (source_id, thinker_id)
  for forward lookups.
* ``ix_content_thinkers_thinker_id`` -- junction reverse lookup (find all
  content for a thinker). PK already covers (content_id, thinker_id).
* ``ix_candidate_thinkers_thinker_id`` -- optional FK to thinkers on
  promotion. Usually null, but scanned by the thinker-detail page.
* ``ix_candidate_thinkers_llm_review_id`` -- FK to llm_reviews.
* ``ix_jobs_llm_review_id``          -- FK to llm_reviews; used by the
  review-detail page to list jobs spawned from a review.

We intentionally do NOT use ``CREATE INDEX CONCURRENTLY`` here. Alembic
migrations run inside a transaction, and CONCURRENTLY is incompatible
with transactional DDL. For the current corpus sizes (<1M content rows)
a locking index build completes in <5s on Railway Postgres. Should we
outgrow that, a follow-up migration can drop-and-recreate concurrently
outside alembic using a raw connection -- but only when needed.

Chains off 008 rather than phase13_cataloged_idx so that the main
migration sequence stays linear; phase13 remains a parallel head that
will be merged separately when its feature ships.
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "009_index_hot_fks"
down_revision: Union[str, Sequence[str], None] = "008_renormalize_urls"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# (index_name, table, columns) tuples. Driving the up/down from a single
# list keeps the pair symmetric and makes it easy to audit coverage.
_FK_INDEXES: tuple[tuple[str, str, list[str]], ...] = (
    ("ix_content_source_id", "content", ["source_id"]),
    ("ix_content_source_owner_id", "content", ["source_owner_id"]),
    ("ix_sources_thinker_id", "sources", ["thinker_id"]),
    ("ix_source_thinkers_thinker_id", "source_thinkers", ["thinker_id"]),
    ("ix_content_thinkers_thinker_id", "content_thinkers", ["thinker_id"]),
    ("ix_candidate_thinkers_thinker_id", "candidate_thinkers", ["thinker_id"]),
    (
        "ix_candidate_thinkers_llm_review_id",
        "candidate_thinkers",
        ["llm_review_id"],
    ),
    ("ix_jobs_llm_review_id", "jobs", ["llm_review_id"]),
)


def upgrade() -> None:
    for index_name, table, columns in _FK_INDEXES:
        op.create_index(index_name, table, columns, unique=False)


def downgrade() -> None:
    # Reverse order is not strictly required (indexes are independent) but
    # keeps the diff symmetric with upgrade().
    for index_name, table, _ in reversed(_FK_INDEXES):
        op.drop_index(index_name, table_name=table)
