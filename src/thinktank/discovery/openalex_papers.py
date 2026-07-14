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

import re
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
# Abstracts shorter than this carry no groundable content (bare stubs).
MIN_ABSTRACT_CHARS = 200

# Version/editorial prefixes OpenAlex attaches to duplicate records of one work.
_TITLE_PREFIX = re.compile(r"^(author response|decision letter|correction|erratum|editorial|reply)\s*[:\-]\s*", re.I)


def normalize_title(title: str) -> str:
    """Collapse a paper title to a stable dedup key.

    Strips editorial/version prefixes ('Author response:', 'Correction:'),
    lowercases, and squeezes non-alphanumerics -- so a preprint, its
    published version, and versioned DOIs of the same work map together.
    """
    stripped = _TITLE_PREFIX.sub("", title.strip())
    return re.sub(r"[^a-z0-9]+", " ", stripped.lower()).strip()


@dataclass
class PaperRecord:
    openalex_id: str  # the work's OpenAlex id (stable dedup key)
    title: str
    abstract: str
    published_at: datetime | None
    landing_url: str  # DOI or OpenAlex URL (canonical/provenance)
    oa_url: str | None = None  # open-access full-text location (W3.3), if any


def _resolve_oa_url(work: dict) -> str | None:
    """The best open-access full-text URL for a work, if it is OA.

    Prefer a direct PDF, then the OA landing page. None when the work is
    not open access (we never fetch paywalled full text)."""
    if not (work.get("open_access") or {}).get("is_oa"):
        return None
    loc = work.get("best_oa_location") or {}
    return loc.get("pdf_url") or loc.get("landing_page_url") or (work.get("open_access") or {}).get("oa_url")


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

    # Dedupe by normalized title, keeping the richest abstract. OpenAlex
    # returns the SAME work as multiple records -- a bioRxiv preprint, the
    # published version, AND versioned DOIs (elife.92092.1/.2/.3) -- each
    # with a distinct DOI, so canonical_url dedup downstream misses them
    # (observed: one Kenyon paper ingested 5x). Also drops near-empty
    # abstracts that carry no groundable content.
    best_by_title: dict[str, PaperRecord] = {}
    for w in works:
        abstract = _reconstruct_abstract(w.get("abstract_inverted_index"))
        if len(abstract.strip()) < MIN_ABSTRACT_CHARS:
            continue
        openalex_id = (w.get("id") or "").rsplit("/", 1)[-1]
        if not openalex_id:
            continue
        title = w.get("title") or w.get("display_name") or "Untitled"
        record = PaperRecord(
            openalex_id=openalex_id,
            title=title,
            abstract=abstract,
            published_at=_parse_date(w.get("publication_date")),
            landing_url=w.get("doi") or w.get("id"),
            oa_url=_resolve_oa_url(w),
        )
        key = normalize_title(title)
        existing = best_by_title.get(key)
        if existing is None or len(record.abstract) > len(existing.abstract):
            best_by_title[key] = record

    papers = list(best_by_title.values())
    logger.info("openalex_papers_fetched", name=name, author_id=author_id, papers=len(papers), raw_works=len(works))
    return papers
