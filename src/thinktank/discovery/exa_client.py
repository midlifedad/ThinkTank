"""Exa client for the inquiry web lane (Web-Lane Hardening W1).

Exa is search-native and returns clean page text + publishedDate +
author in ONE call, which fixes two failures of the old
Perplexity-sonar-then-scrape lane at once:

- the scraper returned 165-char PMC shells / 220-char YouTube pages
  because those sites are JS-rendered or bot-gated; Exa returns real
  extracted text.
- every web observation had asserted_at=NULL because no publication
  date was extracted; Exa returns publishedDate.

Scope (per docs/plans/2026-07-13-web-lane-hardening.md): Exa replaces
Perplexity in the INQUIRY WEB LANE only. Expert *seeding* keeps
Perplexity sonar-deep-research -- Exa does not do that synthesis.

Degrades to [] on missing key / any failure, so the web lane keeps
working (or falls back) rather than failing the inquiry.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from thinktank.http_utils import raise_for_status_with_backoff
from thinktank.models.api_usage import ApiUsage
from thinktank.secrets import get_secret

logger = structlog.get_logger(__name__)

_SEARCH_URL = "https://api.exa.ai/search"
_CONTENTS_URL = "https://api.exa.ai/contents"
_TIMEOUT = httpx.Timeout(connect=15.0, read=60.0, write=15.0, pool=15.0)
# Text beyond this is truncated before storage/extraction (matches
# web_fetch.MAX_TEXT_CHARS -- keeps LLM prompts bounded).
_MAX_TEXT_CHARS = 60_000
# Exa bills per result returned with contents; a search is roughly this.
_SEARCH_COST_USD = 0.01


@dataclass
class ExaResult:
    url: str
    title: str | None
    text: str | None
    published_at: datetime | None
    author: str | None


def _parse_published(raw: str | None) -> datetime | None:
    """Exa publishedDate is ISO-8601 (often just a date). Tolerant parse."""
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _to_results(payload: dict) -> list[ExaResult]:
    out: list[ExaResult] = []
    for r in payload.get("results", []):
        url = r.get("url")
        if not url:
            continue
        text = r.get("text")
        if isinstance(text, str) and text:
            text = text[:_MAX_TEXT_CHARS]
        else:
            text = None
        out.append(
            ExaResult(
                url=url,
                title=r.get("title"),
                text=text,
                published_at=_parse_published(r.get("publishedDate")),
                author=r.get("author"),
            )
        )
    return out


def _record_cost(session: AsyncSession, endpoint: str, results: int, usage_tokens: int | None = None) -> None:
    session.add(
        ApiUsage(
            id=uuid.uuid4(),
            api_name="exa",
            endpoint=endpoint,
            period_start=datetime.now(UTC),
            call_count=1,
            units_consumed=results or None,
            estimated_cost_usd=_SEARCH_COST_USD * max(results, 1),
        )
    )


async def exa_search(session: AsyncSession, query: str, num_results: int = 8) -> list[ExaResult]:
    """One Exa search that returns results WITH clean text + dates.

    Empty list on missing key or any failure (the caller degrades).
    """
    api_key = await get_secret(session, "exa_api_key")
    if not api_key:
        logger.warning("exa_key_missing", hint="seed secret_exa_api_key in system_config")
        return []
    payload = {
        "query": query,
        "numResults": num_results,
        "type": "auto",
        "contents": {"text": {"maxCharacters": _MAX_TEXT_CHARS}},
    }
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(_SEARCH_URL, headers={"x-api-key": api_key}, json=payload)
            raise_for_status_with_backoff(resp)
            body = resp.json()
    except Exception:
        logger.warning("exa_search_failed", query=query[:80], exc_info=True)
        return []
    results = _to_results(body)
    _record_cost(session, "exa_search", len(results))
    logger.info("exa_search_complete", query=query[:80], results=len(results))
    return results


async def exa_contents(session: AsyncSession, url: str) -> ExaResult | None:
    """Fetch clean contents for ONE known URL (web_fetch fallback chain).

    None on missing key / failure / no text.
    """
    api_key = await get_secret(session, "exa_api_key")
    if not api_key:
        return None
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                _CONTENTS_URL,
                headers={"x-api-key": api_key},
                json={"urls": [url], "text": {"maxCharacters": _MAX_TEXT_CHARS}},
            )
            raise_for_status_with_backoff(resp)
            body = resp.json()
    except Exception:
        logger.warning("exa_contents_failed", url=url[:120], exc_info=True)
        return None
    results = _to_results(body)
    _record_cost(session, "exa_contents", len(results))
    result = results[0] if results else None
    return result if result and result.text else None
