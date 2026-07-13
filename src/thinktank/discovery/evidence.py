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
# Wikimedia APIs REQUIRE a descriptive User-Agent -- without one every
# request 403s (the bug that zeroed notability for all longevity experts).
_USER_AGENT = "ThinkTank/1.0 (expert-vetting; thinktank@midlifedad.dev)"


def _norm(s: str) -> str:
    return " ".join(s.lower().split())


def _significant_tokens(name: str) -> set[str]:
    """Name tokens for matching, dropping single-letter initials and dots.

    "David A. Sinclair" -> {david, sinclair}. Lets exact-ish name compares
    survive middle-initial variance between the seed name and each API's
    canonical form (OpenAlex/OpenLibrary list many authors without the
    initial, which made strict substring matching miss them entirely).
    """
    tokens = set()
    for raw in _norm(name).replace(".", " ").split():
        if len(raw) > 1:
            tokens.add(raw)
    return tokens


def _name_matches(query: str, candidate: str) -> bool:
    """True when two names plausibly refer to the same person.

    Symmetric token-subset: one name's significant tokens contain the
    other's (handles "David Sinclair" vs "David A. Sinclair" both ways).
    Requires at least a first+last token to avoid single-surname noise.
    """
    q, c = _significant_tokens(query), _significant_tokens(candidate)
    if len(q) < 2 or not c:
        return False
    return q <= c or c <= q


def _openalex_block(author: dict) -> dict:
    """Scored evidence block for one OpenAlex author record."""
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


def _openalex_option(author: dict) -> dict:
    """Compact one-line summary for the adjudicator (not the raw payload)."""
    institutions = [i.get("display_name") for i in author.get("last_known_institutions", []) if i.get("display_name")]
    topics = [t.get("display_name") for t in (author.get("topics") or [])[:4] if t.get("display_name")]
    return {
        "openalex_id": author.get("id"),
        "name": author.get("display_name"),
        "institution": institutions[0] if institutions else None,
        "citations": author.get("cited_by_count", 0),
        "topics": topics,
    }


async def _openalex(client: httpx.AsyncClient, name: str) -> dict:
    """Author scholarship stats from OpenAlex (free, keyless).

    On ambiguity (no string match despite results, or several distinct
    same-name authors) the block carries ``ambiguous`` + ``options`` for
    the LLM adjudicator; gather_evidence resolves it when context is
    available, otherwise the block stays as-is (found=False for the
    no-match case -- fail safe).
    """
    resp = await client.get(
        "https://api.openalex.org/authors",
        params={"search": name, "per-page": 5, "mailto": _OPENALEX_MAILTO},
    )
    raise_for_status_with_backoff(resp)
    results = resp.json().get("results", [])
    pool = [r for r in results if _name_matches(name, r.get("display_name", ""))]
    if not pool:
        # No string match despite results (accented names, transliteration --
        # Juan Carlos Izpisúa Belmonte failed here). Surface for adjudication.
        if results:
            return {
                "ok": True,
                "found": False,
                "ambiguous": True,
                "options": [_openalex_option(r) for r in results[:5]],
            }
        return {"ok": True, "found": False}
    author = max(pool, key=lambda r: r.get("cited_by_count", 0))
    block = _openalex_block(author)
    # Multiple distinct same-name people -> let the adjudicator pick.
    if len({r.get("id") for r in pool}) > 1:
        block["ambiguous"] = True
        block["options"] = [_openalex_option(r) for r in pool[:5]]
    return block


async def _wikidata(client: httpx.AsyncClient, name: str) -> dict:
    """Notability + identity anchor from Wikidata (free, keyless)."""
    headers = {"User-Agent": _USER_AGENT}
    resp = await client.get(
        "https://www.wikidata.org/w/api.php",
        params={
            "action": "wbsearchentities",
            "search": name,
            "language": "en",
            "type": "item",
            "format": "json",
            "limit": 5,
        },
        headers=headers,
    )
    raise_for_status_with_backoff(resp)
    hits = resp.json().get("search", [])
    match = next((h for h in hits if _name_matches(name, h.get("label", ""))), None)
    if match is None:
        return {"ok": True, "found": False}

    qid = match["id"]
    entity_resp = await client.get(f"https://www.wikidata.org/wiki/Special:EntityData/{qid}.json", headers=headers)
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
    match = next((d for d in docs if _name_matches(name, d.get("name", ""))), None)
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
    # Correct signature: (session, worker_id, person_name); the response is
    # a dict with an "items" list of episodes, not a feed list. Previously
    # this was called search_by_person(name) and errored on every candidate,
    # zeroing the content leg (the reason 100% of longevity experts were
    # auto-rejected). worker_id is a synthetic label for rate-limit rows.
    data = await client.search_by_person(session, worker_id="vetting", person_name=name)
    items = (data or {}).get("items", [])
    # Distinct feeds the person appears on = the availability signal.
    feed_titles = {i.get("feedTitle") for i in items if i.get("feedTitle")}
    return {
        "ok": True,
        "found": bool(feed_titles),
        "appearance_feed_count": len(feed_titles),
        "episode_count": len(items),
        "sample_feeds": list(feed_titles)[:5],
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


async def _adjudicate_openalex(session: AsyncSession, block: dict, ctx: dict) -> dict:
    """Resolve an ambiguous OpenAlex block via the LLM adjudicator."""
    from thinktank.discovery.adjudicator import resolve_entity

    options = block.get("options") or []
    idx, meta = await resolve_entity(
        session,
        candidate_name=ctx["name"],
        search_area=ctx.get("search_area") or "",
        seed_basis=ctx.get("seed_basis"),
        affiliation=ctx.get("affiliation"),
        source="OpenAlex author",
        options=options,
    )
    if idx is None:
        # Adjudicator found no match -> confirmed not-found.
        return {"ok": True, "found": False, "adjudication": meta}
    chosen_id = options[idx].get("openalex_id")
    # Refetch the full record for the chosen id to build a real block.
    async with httpx.AsyncClient(follow_redirects=True, timeout=_TIMEOUT) as client:
        resp = await client.get(chosen_id, params={"mailto": _OPENALEX_MAILTO})
        raise_for_status_with_backoff(resp)
        chosen = _openalex_block(resp.json())
    chosen["adjudication"] = meta
    return chosen


async def gather_evidence(
    session: AsyncSession,
    name: str,
    hints: dict | None = None,
    adjudicate_ctx: dict | None = None,
) -> dict:
    """Assemble the full evidence dossier for one candidate.

    Args:
        session: DB session (PodcastIndex credential lookup, adjudicator).
        name: Candidate's name as surfaced.
        hints: Optional seed-stage platform hints:
            {"youtube_url": ..., "substack_url": ..., "affiliation": ...}
        adjudicate_ctx: When provided ({name, search_area, seed_basis,
            affiliation}), ambiguous structured-source blocks are resolved
            by the LLM adjudicator (Amir 2026-07-12). Absent -> deterministic
            only (ambiguous blocks keep their fail-safe value).

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

    # On-ambiguity LLM adjudication (only when context provided).
    if adjudicate_ctx and dossier.get("openalex", {}).get("ambiguous"):
        try:
            dossier["openalex"] = await _adjudicate_openalex(session, dossier["openalex"], adjudicate_ctx)
        except Exception:
            logger.warning("openalex_adjudication_failed", candidate=name, exc_info=True)

    return dossier
