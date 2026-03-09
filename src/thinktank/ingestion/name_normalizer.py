"""Candidate thinker name normalization per spec Section 5.5 Layer 3.

Pure function -- no I/O, no database, no async.

Normalization steps:
1. Unicode NFC normalize
2. Lowercase
3. Strip titles (Dr., Prof., Ph.D., Jr., Sr., III, II, IV, Mr., Mrs., Ms., Rev.)
4. Collapse whitespace
"""

import re
import unicodedata

_TITLE_PATTERNS = re.compile(
    r"\b(dr|prof|ph\.?d|jr|sr|iii|ii|iv|mr|mrs|ms|rev)\b\.?\s*",
    re.IGNORECASE,
)


def normalize_name(name: str) -> str:
    """Normalize a thinker name for dedup comparison.

    Args:
        name: Raw name string.

    Returns:
        Normalized name: lowercased, titles stripped, NFC unicode, collapsed whitespace.
    """
    # Unicode NFC normalize
    name = unicodedata.normalize("NFC", name)
    # Lowercase
    name = name.lower()
    # Strip titles
    name = _TITLE_PATTERNS.sub("", name)
    # Strip trailing periods/dots left by title removal
    name = name.replace(".", " ")
    # Collapse whitespace
    name = re.sub(r"\s+", " ", name).strip()
    return name
