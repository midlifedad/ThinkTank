"""Structured evidence gathering for expert candidate vetting.

Expert Discovery & Vetting pipeline, Stage 2 (Amir spec 2026-07-12): a
candidate surfaced by seeding (Perplexity deep research / OpenAlex /
metadata mining) is verified against FREE structured APIs -- zero LLM
tokens. Each source contributes one block to the evidence dossier; the
rubric (discovery/rubric.py) turns the dossier into a deterministic
qualification score, and only shortlisted candidates ever reach the LLM
judge.

Error isolation: every source is best-effort. A failed lookup yields
``{"ok": False, "error": ...}`` for that block -- the rubric scores the
leg 0 and the LLM judge sees the gap explicitly. A vetting job never
fails because one external API hiccuped.

Sources:
    openalex      scholarship (citations, works, h-index, institution)
    wikidata      notability + identity anchor (QID, description, sitelinks)
    openlibrary   authored books
    podcastindex  spoken-content availability (podcast appearances)
    youtube       content availability (verified from seed hint only)
    substack      content availability (verified from seed hint only)

YouTube/Substack legs verify SEED HINTS (URLs the deep-research pass
found) rather than searching: name-guessing handles is unreliable and
API search burns quota. No hint -> block marked "unknown", not failed.
"""

from __future__ import annotations

import asyncio

import httpx
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from thinktank.http_utils import raise_for_status_with_backoff
from thinktank.secrets import get_secret

logger = structlog.get_logger(__name__)

_TIMEOUT = 20.0
# OpenAlex polite pool: identify the caller via mailto for higher limits.
_OPENALEX_MAILTO = "thinktank@midlifedad.dev"


def _norm(s: str) -> str:
    return " ".join(s.lower().split())


async def _openalex(client: httpx.AsyncClient, name: str) -> dict:
    """Author scholarship stats from OpenAlex (free, keyless)."""
    resp = await client.get(
        "https://api.openalex.org/authors",
        params={"search": name, "per-page": 5, "mailto": _OPENALEX_MAILTO},
    )
    raise_for_status_with_backoff(resp)
    results = resp.json().get("results", [])
    # Best match: exact normalized display-name hit, else the most-cited
    # candidate whose name contains the query (guards John Smith noise a
    # little; true disambiguation happens at the LLM judge with the QID).
    exact = [r for r in results if _norm(r.get("display_name", "")) == _norm(name)]
    pool = exact or [r for r in results if _norm(name) in _norm(r.get("display_name", ""))]
    if not pool:
        return {"ok": True, "found": False}
    author = max(pool, key=lambda r: r.get("cited_by_count", 0))
    stats = author.get("summary_stats", {})
    institutions = [i.get("display_name") for i in author.get("last_known_institutions", []) if i.get("display_name")]
    topics = [t.get("display_name") for t in (author.get("topics") or [])[:5] if t.get("display_name")]
    return {
        "ok": True,
        "found": True,
        "openalex_id": author.get("id"),
        "display_name": author.get("display_name"),
        "cited_by_count": author.get("cited_by_count", 0),
        "works_count": author.get("works_count", 0),
        "h_index": stats.get("h_index", 0),
        "institutions": institutions,
        "topics": topics,
    }


async def _wikidata(client: httpx.AsyncClient, name: str) -> dict:
    """Notability + identity anchor from Wikidata (free, keyless)."""
    resp = await client.get(
        "https://www.wikidata.org/w/api.php",
        params={
            "action": "wbsearchentities",
            "search": name,
            "language": "en",
            "type": "item",
            "format": "json",
            "limit": 3,
        },
    )
    raise_for_status_with_backoff(resp)
    hits = resp.json().get("search", [])
    match = next((h for h in hits if _norm(h.get("label", "")) == _norm(name)), None)
    if match is None:
        return {"ok": True, "found": False}

    qid = match["id"]
    entity_resp = await client.get(f"https://www.wikidata.org/wiki/Special:EntityData/{qid}.json")
    raise_for_status_with_backoff(entity_resp)
    entity = entity_resp.json().get("entities", {}).get(qid, {})
    sitelinks = entity.get("sitelinks", {})
    return {
        "ok": True,
        "found": True,
        "qid": qid,
        "description": match.get("description"),
        "sitelink_count": len(sitelinks),
        "has_enwiki": "enwiki" in sitelinks,
    }


async def _openlibrary(client: httpx.AsyncClient, name: str) -> dict:
    """Authored books from OpenLibrary (free, keyless)."""
    resp = await client.get("https://openlibrary.org/search/authors.json", params={"q": name})
    raise_for_status_with_backoff(resp)
    docs = resp.json().get("docs", [])
    match = next((d for d in docs if _norm(d.get("name", "")) == _norm(name)), None)
    if match is None:
        return {"ok": True, "found": False}
    return {
        "ok": True,
        "found": True,
        "work_count": match.get("work_count", 0),
        "top_work": match.get("top_work"),
    }


async def _podcast_appearances(session: AsyncSession, name: str) -> dict:
    """Spoken-content availability via PodcastIndex person search."""
    from thinktank.discovery.podcastindex_client import PodcastIndexClient

    api_key = await get_secret(session, "podcastindex_api_key")
    api_secret = await get_secret(session, "podcastindex_api_secret")
    if not api_key or not api_secret:
        return {"ok": False, "error": "podcastindex credentials not configured"}

    client = PodcastIndexClient(api_key, api_secret)
    feeds = await client.search_by_person(name)
    return {
        "ok": True,
        "found": bool(feeds),
        "appearance_feed_count": len(feeds),
        "sample_feeds": [f.get("title") for f in feeds[:5]],
    }


async def _probe_feed_url(client: httpx.AsyncClient, url: str) -> bool:
    """True when a hinted content URL answers (any 2xx/3xx)."""
    try:
        resp = await client.head(url)
        if resp.status_code < 400:
            return True
        resp = await client.get(url)
        return resp.status_code < 400
    except Exception:
        return False


async def gather_evidence(
    session: AsyncSession,
    name: str,
    hints: dict | None = None,
) -> dict:
    """Assemble the full evidence dossier for one candidate.

    Args:
        session: DB session (PodcastIndex credential lookup).
        name: Candidate's name as surfaced.
        hints: Optional seed-stage platform hints:
            {"youtube_url": ..., "substack_url": ..., "affiliation": ...}

    Returns:
        Dossier dict with one block per source. Blocks from failed
        lookups carry {"ok": False, "error": ...}; hint-dependent blocks
        without hints carry {"ok": True, "checked": False}.
    """
    hints = hints or {}
    dossier: dict = {"name": name, "hints": hints}

    async with httpx.AsyncClient(follow_redirects=True, timeout=_TIMEOUT) as client:

        async def _isolated(key: str, coro) -> None:
            try:
                dossier[key] = await coro
            except Exception as exc:
                logger.warning("evidence_source_failed", source=key, candidate=name, error=str(exc)[:120])
                dossier[key] = {"ok": False, "error": str(exc)[:200]}

        await asyncio.gather(
            _isolated("openalex", _openalex(client, name)),
            _isolated("wikidata", _wikidata(client, name)),
            _isolated("openlibrary", _openlibrary(client, name)),
            _isolated("podcastindex", _podcast_appearances(session, name)),
        )

        for key, hint_field in (("youtube", "youtube_url"), ("substack", "substack_url")):
            url = hints.get(hint_field)
            if not url:
                dossier[key] = {"ok": True, "checked": False}
            else:
                reachable = await _probe_feed_url(client, url)
                dossier[key] = {"ok": True, "checked": True, "url": url, "reachable": reachable}

    return dossier
