"""Fetch an author's recent papers from OpenAlex (Web-Lane Hardening W3.2).

The academic experts the rapamycin inquiry left `unknown` (Kenyon,
Church, Gladyshev, ...) publish papers, not podcasts. Their content is on
OpenAlex, free and structured. This module resolves an author by name and
returns their recent works with the abstract reconstructed from OpenAlex's
inverted index -- a dense, dated, quotable summary of each paper's claims,
ideal grounding material for the claims/inquiry engine.

Abstracts (always present) are used, not full text: they are reliable and
high-signal, and a prolific scientist yields dozens of dated, attributed
claim-bearing paragraphs. OA full-text PDF extraction is a later refinement.

No API key required (OpenAlex is open; we send a mailto for the polite pool).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
import structlog

from thinktank.http_utils import raise_for_status_with_backoff

logger = structlog.get_logger(__name__)

_TIMEOUT = 25.0
_MAILTO = "thinktank@midlifedad.dev"
_AUTHORS_URL = "https://api.openalex.org/authors"
_WORKS_URL = "https://api.openalex.org/works"


@dataclass
class PaperRecord:
    openalex_id: str  # the work's OpenAlex id (stable dedup key)
    title: str
    abstract: str
    published_at: datetime | None
    landing_url: str  # DOI or OpenAlex URL (canonical/provenance)


def _reconstruct_abstract(inverted: dict | None) -> str:
    """OpenAlex stores abstracts as {word: [positions]}; rebuild the text."""
    if not inverted:
        return ""
    positioned: list[tuple[int, str]] = []
    for word, positions in inverted.items():
        for p in positions:
            positioned.append((p, word))
    positioned.sort()
    return " ".join(word for _, word in positioned)


def _parse_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw).replace(tzinfo=UTC)
    except (ValueError, TypeError):
        return None


async def _resolve_author_id(client: httpx.AsyncClient, name: str) -> str | None:
    """Best-effort author id for a name (top result on the search endpoint).

    Disambiguation risk on common names is real; the caller treats papers
    as author-attributed on the strength of this match, so a later
    refinement could cross-check institution/topic against the dossier.
    """
    resp = await client.get(_AUTHORS_URL, params={"search": name, "per-page": 1, "mailto": _MAILTO})
    raise_for_status_with_backoff(resp)
    results = resp.json().get("results", [])
    return results[0]["id"].rsplit("/", 1)[-1] if results else None


async def fetch_author_papers(name: str, limit: int = 25, since_year: int = 2015) -> list[PaperRecord]:
    """Recent papers (newest first) for an author, with reconstructed
    abstracts. Empty list on no-match / failure (the caller degrades)."""
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            author_id = await _resolve_author_id(client, name)
            if not author_id:
                logger.info("openalex_papers_no_author", name=name)
                return []
            works_resp = await client.get(
                _WORKS_URL,
                params={
                    "filter": f"author.id:{author_id},from_publication_date:{since_year}-01-01",
                    "sort": "publication_date:desc",
                    "per-page": limit,
                    "mailto": _MAILTO,
                },
            )
            raise_for_status_with_backoff(works_resp)
            works = works_resp.json().get("results", [])
    except Exception:
        logger.warning("openalex_papers_failed", name=name, exc_info=True)
        return []

    papers: list[PaperRecord] = []
    for w in works:
        abstract = _reconstruct_abstract(w.get("abstract_inverted_index"))
        if not abstract.strip():
            continue  # no abstract -> nothing to ground on
        openalex_id = (w.get("id") or "").rsplit("/", 1)[-1]
        if not openalex_id:
            continue
        papers.append(
            PaperRecord(
                openalex_id=openalex_id,
                title=w.get("title") or w.get("display_name") or "Untitled",
                abstract=abstract,
                published_at=_parse_date(w.get("publication_date")),
                landing_url=w.get("doi") or w.get("id"),
            )
        )
    logger.info("openalex_papers_fetched", name=name, author_id=author_id, papers=len(papers))
    return papers
