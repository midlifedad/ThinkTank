"""Regex-based person name extraction from podcast episode metadata.

Pure function -- no I/O, no async, no database.

Scans episode titles and descriptions for common podcast guest name
patterns (e.g., "with John Smith", "feat. Jane Doe", "#123 - Alice Walker").
Validates extracted names with structural checks and a blocklist,
then normalizes via the existing name_normalizer.
"""

import re

from src.thinktank.ingestion.name_normalizer import normalize_name

# Regex to strip honorific titles from text before pattern matching.
# Matches "Dr.", "Prof.", "Mr.", "Mrs.", "Ms.", "Rev." and similar.
_TITLE_STRIP = re.compile(
    r"\b(?:Dr|Prof|Mr|Mrs|Ms|Rev|Ph\.?D|Jr|Sr)\.?\s*",
    re.IGNORECASE,
)

# Name-word pattern: Capitalized word (uppercase first, lowercase rest, 2+ chars).
# NOT case-insensitive -- proper capitalization is a signal that it's a name.
_NAME = r"[A-Z][a-z]+"
_FULL_NAME = rf"{_NAME}(?:\s+{_NAME})+"

# Common patterns in podcast episode titles/descriptions.
# Each pattern captures a person name in group(1).
# Keywords use (?i:...) inline flag for case-insensitive matching,
# but name capture requires proper Title Case to reduce false positives.
_GUEST_PATTERNS = [
    # "with John Smith" / "w/ John Smith"
    re.compile(rf"(?i:with|w/)\s+({_FULL_NAME})"),
    # "feat. Jane Doe" / "feat Jane Doe" / "featuring Jane Doe"
    re.compile(rf"(?i:feat\.?|featuring)\s+({_FULL_NAME})"),
    # "Interview: John Smith" / "Guest: John Smith" / "Conversation: John Smith"
    re.compile(rf"(?i:interview|guest|conversation)[:\s]+({_FULL_NAME})"),
    # "John Smith on Topic" / "John Smith talks..." / "John Smith discusses..."
    re.compile(rf"^({_FULL_NAME})\s+(?i:on|talks|discusses|explains)"),
    # "#123 - John Smith" / "123 - John Smith" (hyphen, en-dash, em-dash, or colon)
    re.compile(rf"#?\d+\s*[-\u2013\u2014:]\s*({_FULL_NAME})"),
    # "| Sam Harris"
    re.compile(rf"\|\s*({_FULL_NAME})"),
]

# Words that indicate the match is not a person name.
_BLOCKLIST = frozenset({
    "the", "inc", "llc", "university", "foundation", "institute",
    "network", "podcast", "show", "episode", "series", "season",
    "part", "chapter", "volume",
})


def _looks_like_person_name(name: str) -> bool:
    """Validate that a matched string looks like a person name.

    Rules:
    - 2-4 words
    - Each word >= 2 characters
    - No all-caps words (length > 1) -- likely acronyms or shouted titles
    - No blocklist words (lowercased)
    """
    parts = name.split()
    if not (2 <= len(parts) <= 4):
        return False
    for part in parts:
        if len(part) < 2:
            return False
        # Reject all-caps words (len > 1) -- likely acronyms/titles
        if len(part) > 1 and part.isupper():
            return False
        if part.lower() in _BLOCKLIST:
            return False
    return True


def extract_names(title: str, description: str) -> list[str]:
    """Extract candidate person names from episode metadata.

    Scans both title and description through all guest-name regex patterns.
    Validates matches with structural checks, normalizes via normalize_name(),
    and returns a deduplicated sorted list.

    Args:
        title: Episode title text.
        description: Episode description text.

    Returns:
        Sorted list of normalized person names (deduplicated).
    """
    names: set[str] = set()
    for text in [title, description]:
        if not text:
            continue
        # Strip honorific titles before pattern matching so "Dr. Bob Jones"
        # becomes "Bob Jones" and matches the name-capture regex.
        cleaned = _TITLE_STRIP.sub("", text)
        # Collapse any resulting double spaces.
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        for pattern in _GUEST_PATTERNS:
            for match in pattern.finditer(cleaned):
                raw = match.group(1).strip()
                if _looks_like_person_name(raw):
                    names.add(normalize_name(raw))
    return sorted(names)
