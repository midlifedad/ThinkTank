"""Thinker name matching in episode text per spec Section 6.6.

Pure function -- no I/O, no database, no async.

Attribution rules:
- Source owner: role='primary', confidence=10
- Title exact name match: role='guest', confidence=9
- Description exact name match: role='guest', confidence=6
- Full name matching only (not partial first/last for v1)
- Title match takes precedence over description match (higher confidence wins)
"""

import re
import uuid


def _name_pattern(name: str) -> re.Pattern[str]:
    """Compile a case-insensitive word-boundary pattern for a full name.

    Uses \\b anchors so "Sam Harris" does not match "Scam Harrison" and
    "Dan" does not match "Dangerfield" (HANDLERS-REVIEW ME-01).
    """
    return re.compile(rf"\b{re.escape(name)}\b", re.IGNORECASE)


def match_thinkers_in_text(
    title: str,
    description: str,
    thinker_names: list[dict],
    source_owner_name: str | None,
) -> list[dict]:
    """Match thinker names in episode title and description.

    Uses word-boundary regex to prevent substring false-positives
    (e.g. "sam harris" matching "Scam Harrison").

    Args:
        title: Episode title.
        description: Episode description.
        thinker_names: List of dicts with {"id": uuid, "name": str}.
        source_owner_name: Name of the source owner (tagged as primary).

    Returns:
        List of dicts with {"thinker_id": uuid, "role": str, "confidence": int}.
        Each thinker appears at most once (highest confidence wins).
    """
    results: dict[uuid.UUID, dict] = {}
    title = title or ""
    description = description or ""

    for thinker in thinker_names:
        thinker_id = thinker["id"]
        name = thinker["name"]

        # Check if this is the source owner (exact equality, case-insensitive)
        if source_owner_name and name.lower() == source_owner_name.lower():
            results[thinker_id] = {
                "thinker_id": thinker_id,
                "role": "primary",
                "confidence": 10,
            }
            continue

        pattern = _name_pattern(name)

        # Check title (word-boundary match, case-insensitive)
        if pattern.search(title):
            results[thinker_id] = {
                "thinker_id": thinker_id,
                "role": "guest",
                "confidence": 9,
            }
            continue

        # Check description (word-boundary match, case-insensitive)
        if pattern.search(description):
            results[thinker_id] = {
                "thinker_id": thinker_id,
                "role": "guest",
                "confidence": 6,
            }

    return list(results.values())
