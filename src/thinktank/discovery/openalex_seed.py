"""OpenAlex top-cited author seeding for expert search (zero tokens, v1).

Second seed lane alongside Perplexity deep research: resolves the area to
OpenAlex topics, then pulls the most-cited living-ish authors publishing
in them. Catches academic heavyweights that media-centric research
under-surfaces. Free, keyless, no LLM involvement.
"""

from __future__ import annotations

import httpx
import structlog

from thinktank.http_utils import raise_for_status_with_backoff

logger = structlog.get_logger(__name__)

_TIMEOUT = 20.0
_MAILTO = "thinktank@midlifedad.dev"
# Only surface authors with recent output -- "living experts", not the
# most-cited names of 1980.
_RECENT_YEAR_FLOOR = 2020


async def seed_from_openalex(area: str, limit: int = 15) -> list[dict]:
    """Top-cited recently-active authors for an area.

    Returns:
        Expert claim dicts shaped like the Perplexity lane's output
        (name, basis, affiliation) so the seed handler treats both lanes
        uniformly. Empty list on failure.
    """
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            topic_resp = await client.get(
                "https://api.openalex.org/topics",
                params={"search": area, "per-page": 3, "mailto": _MAILTO},
            )
            raise_for_status_with_backoff(topic_resp)
            topics = topic_resp.json().get("results", [])
            if not topics:
                logger.info("openalex_seed_no_topics", area=area)
                return []
            topic_ids = "|".join(t["id"].rsplit("/", 1)[-1] for t in topics)

            author_resp = await client.get(
                "https://api.openalex.org/authors",
                params={
                    "filter": (
                        f"topics.id:{topic_ids},"
                        f"last_known_institutions.id:!null,"
                        f"counts_by_year.year:>{_RECENT_YEAR_FLOOR - 1}"
                    ),
                    "sort": "cited_by_count:desc",
                    "per-page": limit,
                    "mailto": _MAILTO,
                },
            )
            raise_for_status_with_backoff(author_resp)
            authors = author_resp.json().get("results", [])
    except Exception:
        logger.warning("openalex_seed_failed", area=area, exc_info=True)
        return []

    experts = []
    for author in authors:
        institutions = [
            i.get("display_name") for i in author.get("last_known_institutions", []) if i.get("display_name")
        ]
        experts.append(
            {
                "name": author.get("display_name"),
                "basis": f"OpenAlex top-cited in {area} ({author.get('cited_by_count', 0):,} citations)",
                "affiliation": institutions[0] if institutions else None,
            }
        )
    logger.info("openalex_seed_complete", area=area, experts=len(experts))
    return experts
