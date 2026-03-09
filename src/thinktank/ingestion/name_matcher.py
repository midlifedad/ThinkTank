"""Thinker name matching in episode text per spec Section 6.6.

Pure function -- no I/O, no database, no async.

Attribution rules:
- Source owner: role='primary', confidence=10
- Title exact name match: role='guest', confidence=9
- Description exact name match: role='guest', confidence=6
- Full name matching only (not partial first/last for v1)
- Title match takes precedence over description match (higher confidence wins)
"""

import uuid


def match_thinkers_in_text(
    title: str,
    description: str,
    thinker_names: list[dict],
    source_owner_name: str | None,
) -> list[dict]:
    """Match thinker names in episode title and description.

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
    title_lower = title.lower()
    desc_lower = description.lower()

    for thinker in thinker_names:
        thinker_id = thinker["id"]
        name = thinker["name"]
        name_lower = name.lower()

        # Check if this is the source owner
        if source_owner_name and name_lower == source_owner_name.lower():
            results[thinker_id] = {
                "thinker_id": thinker_id,
                "role": "primary",
                "confidence": 10,
            }
            continue

        # Check title (full name match, case-insensitive)
        if name_lower in title_lower:
            results[thinker_id] = {
                "thinker_id": thinker_id,
                "role": "guest",
                "confidence": 9,
            }
            continue

        # Check description (full name match, case-insensitive)
        if name_lower in desc_lower:
            results[thinker_id] = {
                "thinker_id": thinker_id,
                "role": "guest",
                "confidence": 6,
            }

    return list(results.values())
