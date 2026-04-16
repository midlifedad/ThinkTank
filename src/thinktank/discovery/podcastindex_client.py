"""Podcast Index API client with SHA-1 auth and rate limit integration.

Thin httpx wrapper for the Podcast Index search API. Generates fresh
authentication headers per request to avoid token expiry issues.

Spec reference: Section 5.4 (guest discovery).
"""

import hashlib
import time

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from src.thinktank.queue.rate_limiter import check_and_acquire_rate_limit


def _podcastindex_headers(api_key: str, api_secret: str) -> dict[str, str]:
    """Generate authentication headers for Podcast Index API.

    Creates a fresh SHA-1 auth token using the current epoch time.
    Must be called per-request (tokens have a 3-minute validity window).

    Args:
        api_key: Podcast Index API key.
        api_secret: Podcast Index API secret.

    Returns:
        Dict with User-Agent, X-Auth-Key, X-Auth-Date, Authorization headers.
    """
    epoch_time = str(int(time.time()))
    data_to_hash = api_key + api_secret + epoch_time
    sha1_hash = hashlib.sha1(data_to_hash.encode("utf-8")).hexdigest()  # noqa: S324
    return {
        "User-Agent": "ThinkTank/1.0",
        "X-Auth-Key": api_key,
        "X-Auth-Date": epoch_time,
        "Authorization": sha1_hash,
    }


class PodcastIndexClient:
    """Client for Podcast Index podcast search API."""

    BASE_URL = "https://api.podcastindex.org/api/1.0"

    def __init__(self, api_key: str, api_secret: str) -> None:
        self._api_key = api_key
        self._api_secret = api_secret

    async def search_by_person(
        self,
        session: AsyncSession,
        worker_id: str,
        person_name: str,
    ) -> dict | None:
        """Search for episodes by person name.

        Checks rate limit before making the API call. Generates fresh
        auth headers per request to avoid token expiry.

        Args:
            session: Async database session for rate limit check.
            worker_id: Identifier of the calling worker.
            person_name: Name to search for.

        Returns:
            Parsed JSON response dict, or None if rate-limited.

        Raises:
            httpx.HTTPStatusError: On 4xx/5xx API responses.
        """
        if not await check_and_acquire_rate_limit(session, "podcastindex", worker_id):
            return None

        headers = _podcastindex_headers(self._api_key, self._api_secret)

        async with httpx.AsyncClient(follow_redirects=True) as client:
            response = await client.get(
                f"{self.BASE_URL}/search/byperson",
                params={"q": person_name},
                headers=headers,
                timeout=30.0,
            )
            response.raise_for_status()
            return response.json()
