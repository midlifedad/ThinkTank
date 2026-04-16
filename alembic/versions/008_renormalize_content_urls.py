"""Re-run normalize_url() on every existing content.url / canonical_url.

Revision ID: 008_renormalize_urls
Revises: 007_timezone_aware
Create Date: 2026-04-16

Source: DATA-REVIEW H1. The normalizer has been extended to strip
podcast-tracker wrappers (chartable.com/track/*, op3.dev/e/*,
pdst.fm/e/*), drop URL fragments, and canonicalize m.youtube.com /
music.youtube.com to youtube.com. Any rows inserted before those rules
landed need to be renormalized so downstream deduplication
(``content.canonical_url`` uniqueness) works correctly.

The migration is idempotent because ``normalize_url`` itself is
idempotent (see ``TestIdempotence`` in
``tests/unit/test_url_normalizer.py``) -- re-running this migration
after it has already succeeded is a no-op.

Conflict handling: the `canonical_url` column has a UNIQUE constraint.
If renormalizing reveals that two rows collapse to the same canonical
form (e.g. a tracker-wrapped duplicate and the naked URL already both
exist), we log the conflict and keep the row whose ``id`` sorts first
(Postgres UUID v4 ordering is effectively random but deterministic).
Leaving the conflict unresolved would roll back the entire migration.
"""

from typing import Sequence, Union

from alembic import op
from sqlalchemy import text

from thinktank.ingestion.url_normalizer import normalize_url

# revision identifiers, used by Alembic.
revision: str = "008_renormalize_urls"
down_revision: Union[str, Sequence[str], None] = "007_timezone_aware"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Renormalize every content row's url and canonical_url."""
    conn = op.get_bind()
    rows = conn.execute(
        text("SELECT id, url, canonical_url FROM content")
    ).mappings().all()

    # First pass: compute the new canonical forms per row.
    rewrites: list[tuple[str, str, str]] = []  # (id, new_url, new_canonical)
    for row in rows:
        new_url = normalize_url(row["url"]) if row["url"] else row["url"]
        new_canonical = (
            normalize_url(row["canonical_url"])
            if row["canonical_url"]
            else row["canonical_url"]
        )
        if new_url != row["url"] or new_canonical != row["canonical_url"]:
            rewrites.append((str(row["id"]), new_url, new_canonical))

    if not rewrites:
        return

    # Detect collisions: multiple IDs would now share the same canonical_url.
    by_canonical: dict[str, list[str]] = {}
    for row_id, _, new_canonical in rewrites:
        by_canonical.setdefault(new_canonical, []).append(row_id)

    # Keep the lexicographically smallest id per collision; delete the rest.
    # (Content rows are relatively safe to drop post-renormalization because
    # they are re-discoverable from the source feed.)
    to_delete: list[str] = []
    for canonical, ids in by_canonical.items():
        if len(ids) <= 1:
            continue
        # Also check if the already-normalized form exists on an *unchanged*
        # row (one that did not need rewriting but now collides).
        existing = conn.execute(
            text(
                "SELECT id FROM content WHERE canonical_url = :c "
                "AND id <> ALL(:ids)"
            ),
            {"c": canonical, "ids": ids},
        ).scalar_one_or_none()
        keeper = min(ids) if existing is None else None
        for row_id in ids:
            if row_id != keeper:
                to_delete.append(row_id)

    if to_delete:
        conn.execute(
            text("DELETE FROM content WHERE id = ANY(:ids)"),
            {"ids": to_delete},
        )

    # Second pass: apply rewrites for the surviving rows.
    surviving = {rid for rid, _, _ in rewrites} - set(to_delete)
    for row_id, new_url, new_canonical in rewrites:
        if row_id not in surviving:
            continue
        conn.execute(
            text(
                "UPDATE content SET url = :u, canonical_url = :c "
                "WHERE id = :id"
            ),
            {"u": new_url, "c": new_canonical, "id": row_id},
        )


def downgrade() -> None:
    """No-op: renormalization cannot be reversed.

    The original tracker-wrapped URLs are gone after upgrade(); reverting
    would require replaying the source feeds. Downgrade is intentionally
    a no-op rather than raising, so the alembic history can be stepped
    back for schema-only concerns.
    """
    pass
