"""Duration string parsing for itunes:duration values.

Pure function -- no I/O, no database, no async.

Handles: "01:30:00" (HH:MM:SS), "90:00" (MM:SS), "5400" (raw seconds), None.
Returns None if unparseable.
"""

import re

_HMS_RE = re.compile(r"^(\d+):(\d{2}):(\d{2})$")  # HH:MM:SS
_MS_RE = re.compile(r"^(\d+):(\d{2})$")  # MM:SS


def parse_duration(raw: str | None) -> int | None:
    """Parse an itunes:duration string to seconds.

    Args:
        raw: Duration string in HH:MM:SS, MM:SS, or raw seconds format.
             None returns None.

    Returns:
        Duration in seconds, or None if unparseable.
    """
    if raw is None:
        return None

    raw = raw.strip()
    if not raw:
        return None

    # Try HH:MM:SS
    m = _HMS_RE.match(raw)
    if m:
        return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))

    # Try MM:SS
    m = _MS_RE.match(raw)
    if m:
        return int(m.group(1)) * 60 + int(m.group(2))

    # Try raw seconds
    try:
        return int(raw)
    except ValueError:
        return None
