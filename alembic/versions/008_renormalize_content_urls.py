"""Re-run normalize_url() on every existing content.url / canonical_url.

Revision ID: 008_renormalize_urls
Revises: 007c_tz_remaining
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

Conflict handling (updated per Troy's deploy-ordering review):

When two rows collapse to the same canonical form, we must pick one
survivor. The previous policy used ``min(ids)`` (lexicographic UUID
sort), which is arbitrary -- it can discard a content row that has
tagged thinkers / review history in favor of an untagged duplicate.

New policy:

1. Prefer the row with the most downstream ``content_thinkers``
   associations (tagged thinkers represent accumulated curation work
   that cannot be cheaply recomputed).
2. Tie-break on the most recent ``discovered_at`` (newer normalizer
   output is more likely to match what ingestion writes going forward).
3. Final tie-break on the lexicographically smallest id (deterministic
   across runs).

Before deleting the losers, we re-point their ``content_thinkers``
rows onto the keeper using ``INSERT ... ON CONFLICT DO NOTHING`` (the
junction has PK ``(content_id, thinker_id)``, so duplicates from the
merge are silently dropped). This preserves thinker-tagging history.

If there is no keeper (the canonical form already exists on a row
outside the rewrite set), the entire collision group is dropped after
merging their associations onto that pre-existing row.
"""

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

from thinktank.ingestion.url_normalizer import normalize_url

# revision identifiers, used by Alembic.
revision: str = "008_renormalize_urls"
down_revision: str | Sequence[str] | None = "007c_tz_remaining"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _pick_keeper(
    conn,
    candidate_ids: list[str],
) -> str:
    """Return the id with most content_thinkers, newest discovered_at, smallest id."""
    # Materialize candidate metrics: (association count, discovered_at, id).
    metrics = conn.execute(
        text(
            "SELECT c.id, "
            "       COALESCE(COUNT(ct.thinker_id), 0) AS assoc_count, "
            "       c.discovered_at "
            "FROM content c "
            "LEFT JOIN content_thinkers ct ON ct.content_id = c.id "
            "WHERE c.id = ANY(:ids) "
            "GROUP BY c.id, c.discovered_at"
        ),
        {"ids": candidate_ids},
    ).all()
    # Sort: associations DESC, discovered_at DESC (None last), id ASC.
    metrics.sort(
        key=lambda r: (
            -int(r[1] or 0),
            -(r[2].timestamp() if r[2] is not None else float("-inf")),
            str(r[0]),
        )
    )
    return str(metrics[0][0])


def _merge_associations(conn, keeper_id: str, loser_ids: list[str]) -> None:
    """Re-point content_thinkers from losers onto keeper, dedupe by PK."""
    if not loser_ids:
        return
    conn.execute(
        text(
            "INSERT INTO content_thinkers (content_id, thinker_id, role, confidence, added_at) "
            "SELECT :keeper, ct.thinker_id, ct.role, ct.confidence, ct.added_at "
            "FROM content_thinkers ct "
            "WHERE ct.content_id = ANY(:losers) "
            "ON CONFLICT (content_id, thinker_id) DO NOTHING"
        ),
        {"keeper": keeper_id, "losers": loser_ids},
    )
    conn.execute(
        text("DELETE FROM content_thinkers WHERE content_id = ANY(:losers)"),
        {"losers": loser_ids},
    )


def upgrade() -> None:
    """Renormalize every content row's url and canonical_url."""
    conn = op.get_bind()
    rows = conn.execute(text("SELECT id, url, canonical_url FROM content")).mappings().all()

    # First pass: compute the new canonical forms per row.
    rewrites: list[tuple[str, str, str]] = []  # (id, new_url, new_canonical)
    for row in rows:
        new_url = normalize_url(row["url"]) if row["url"] else row["url"]
        new_canonical = normalize_url(row["canonical_url"]) if row["canonical_url"] else row["canonical_url"]
        if new_url != row["url"] or new_canonical != row["canonical_url"]:
            rewrites.append((str(row["id"]), new_url, new_canonical))

    if not rewrites:
        return

    # Detect collisions: multiple IDs would now share the same canonical_url.
    by_canonical: dict[str, list[str]] = {}
    for row_id, _, new_canonical in rewrites:
        if new_canonical is None:
            continue
        by_canonical.setdefault(new_canonical, []).append(row_id)

    to_delete: list[str] = []
    for canonical, ids in by_canonical.items():
        # Is there a row outside the rewrite set that already has this canonical?
        existing = conn.execute(
            text("SELECT id FROM content WHERE canonical_url = :c AND id <> ALL(:ids)"),
            {"c": canonical, "ids": ids},
        ).scalar_one_or_none()

        if existing is not None:
            # Pre-existing row wins -- merge everyone in `ids` onto it.
            keeper = str(existing)
            losers = ids
        elif len(ids) > 1:
            # Internal collision -- pick the best survivor from `ids`.
            keeper = _pick_keeper(conn, ids)
            losers = [i for i in ids if i != keeper]
        else:
            continue  # single row, no collision

        _merge_associations(conn, keeper, losers)
        to_delete.extend(losers)

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
            text("UPDATE content SET url = :u, canonical_url = :c WHERE id = :id"),
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
