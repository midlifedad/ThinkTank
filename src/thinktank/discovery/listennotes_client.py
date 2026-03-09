"""Listen Notes API client with rate limit integration.

Thin httpx wrapper for the Listen Notes search API. Uses the existing
rate limiter to coordinate API calls across concurrent workers.

Spec reference: Section 5.4 (guest discovery).
"""

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from src.thinktank.queue.rate_limiter import check_and_acquire_rate_limit


class ListenNotesClient:
    """Client for Listen Notes podcast search API."""

    BASE_URL = "https://listen-api.listennotes.com/api/v2"

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    async def search_episodes_by_person(
        self,
        session: AsyncSession,
        worker_id: str,
        person_name: str,
        offset: int = 0,
    ) -> dict | None:
        """Search for episodes featuring a person.

        Checks rate limit before making the API call. If rate-limited,
        returns None (caller should back off or reschedule).

        Args:
            session: Async database session for rate limit check.
            worker_id: Identifier of the calling worker.
            person_name: Name to search for.
            offset: Pagination offset (default 0).

        Returns:
            Parsed JSON response dict, or None if rate-limited.

        Raises:
            httpx.HTTPStatusError: On 4xx/5xx API responses.
        """
        if not await check_and_acquire_rate_limit(session, "listennotes", worker_id):
            return None

        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.BASE_URL}/search",
                params={"q": person_name, "type": "episode", "offset": offset},
                headers={"X-ListenAPI-Key": self._api_key},
                timeout=30.0,
            )
            response.raise_for_status()
            return response.json()
