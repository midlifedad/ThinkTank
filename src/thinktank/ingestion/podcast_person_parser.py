"""Extract podcast:person XML tags from RSS feed XML.

Pure function -- no I/O, no database, no async.

Parses the Podcast Index namespace (https://podcastindex.org/namespace/1.0)
to extract person elements with name, role, group, href, and img fields
from each RSS item, keyed by GUID.

Size guard: rejects XML > 10MB to prevent resource exhaustion.
"""

import xml.etree.ElementTree as ET

import structlog

logger = structlog.get_logger(__name__)

PODCAST_NS = "https://podcastindex.org/namespace/1.0"

# Maximum XML content size in bytes (10MB)
MAX_XML_SIZE = 10_000_000


def extract_podcast_persons(xml_content: str) -> dict[str, list[dict]]:
    """Extract podcast:person tags from RSS XML, grouped by item GUID.

    Args:
        xml_content: Raw RSS XML string.

    Returns:
        Dict mapping item GUID -> list of person dicts with keys:
        name, role, group, href, img.
        Returns empty dict for oversized, empty, or unparseable XML.
    """
    if not xml_content or not xml_content.strip():
        return {}

    if len(xml_content) > MAX_XML_SIZE:
        logger.warning(
            "podcast_person_parser_oversized_xml",
            size=len(xml_content),
            max_size=MAX_XML_SIZE,
        )
        return {}

    try:
        root = ET.fromstring(xml_content)
    except ET.ParseError:
        logger.warning("podcast_person_parser_xml_parse_error")
        return {}

    result: dict[str, list[dict]] = {}
    person_tag = f"{{{PODCAST_NS}}}person"

    for item in root.iter("item"):
        guid_el = item.find("guid")
        if guid_el is None or not guid_el.text:
            continue

        guid = guid_el.text.strip()
        persons: list[dict] = []

        for person_el in item.findall(person_tag):
            name = (person_el.text or "").strip()
            if not name:
                continue

            persons.append({
                "name": name,
                "role": (person_el.get("role") or "host").lower(),
                "group": (person_el.get("group") or "cast").lower(),
                "href": person_el.get("href"),
                "img": person_el.get("img"),
            })

        if persons:
            result[guid] = persons

    return result
