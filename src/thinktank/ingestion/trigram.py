"""Trigram similarity queries using PostgreSQL pg_trgm extension.

Provides candidate deduplication and thinker-match checking via
trigram similarity at a configurable threshold (default 0.7).

These functions use raw SQL via text() because pg_trgm's similarity()
function is not expressible through SQLAlchemy's ORM layer.

Spec reference: Section 5.5 Layer 3 (DISC-04).
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def find_similar_candidates(
    session: AsyncSession,
    normalized_name: str,
    threshold: float = 0.7,
) -> list[tuple[str, str, float]]:
    """Find candidate_thinkers with similar normalized_name using pg_trgm.

    Args:
        session: Active database session.
        normalized_name: The normalized name to search for.
        threshold: Minimum similarity score (0.0-1.0). Default 0.7.

    Returns:
        List of (id, name, similarity_score) tuples, ordered by similarity descending.
        IDs are cast to str for consistency.
    """
    result = await session.execute(
        text("""
            SELECT id, name, similarity(normalized_name, :name) AS sml
            FROM candidate_thinkers
            WHERE similarity(normalized_name, :name) > :threshold
            ORDER BY sml DESC
        """),
        {"name": normalized_name, "threshold": threshold},
    )
    return [(str(row[0]), row[1], float(row[2])) for row in result.fetchall()]


async def find_similar_thinkers(
    session: AsyncSession,
    normalized_name: str,
    threshold: float = 0.7,
) -> list[tuple[str, str, float]]:
    """Check if a candidate name matches an existing thinker using pg_trgm.

    Prevents candidates that already exist as thinkers under a
    different name variation (e.g. "Dr. John Smith" vs "John Smith").

    Args:
        session: Active database session.
        normalized_name: The normalized name to search for.
        threshold: Minimum similarity score (0.0-1.0). Default 0.7.

    Returns:
        List of (id, name, similarity_score) tuples, ordered by similarity descending.
        IDs are cast to str for consistency.
    """
    result = await session.execute(
        text("""
            SELECT id, name, similarity(lower(name), :name) AS sml
            FROM thinkers
            WHERE similarity(lower(name), :name) > :threshold
            ORDER BY sml DESC
        """),
        {"name": normalized_name, "threshold": threshold},
    )
    return [(str(row[0]), row[1], float(row[2])) for row in result.fetchall()]
