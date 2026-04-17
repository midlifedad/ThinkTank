"""YouTube Data API v3 client for channel video cataloging.

Uses quota-efficient endpoints:
- playlistItems.list: 1 quota unit per page (50 items)
- videos.list: 1 quota unit per batch (50 video IDs)
NOT search.list (100 units per call).

Synchronous client -- the Google API client library is sync.
Handler calls via asyncio.to_thread or directly.
"""

from __future__ import annotations

import re

import structlog

logger = structlog.get_logger(__name__)

# YouTube category IDs to skip (clearly non-interview content)
SKIP_CATEGORY_IDS: set[str] = {"10", "20", "17"}  # Music, Gaming, Sports

# ISO 8601 duration regex: PT1H23M45S, PT45M, PT2H30S, etc.
_ISO_DURATION_RE = re.compile(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?")


def _parse_iso_duration(iso: str) -> int | None:
    """Parse ISO 8601 duration string to seconds.

    Args:
        iso: Duration string like "PT1H23M45S", "PT45M", "PT2H30S".

    Returns:
        Duration in seconds, or None if parsing fails.
    """
    match = _ISO_DURATION_RE.match(iso)
    if not match:
        return None

    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    seconds = int(match.group(3) or 0)
    return hours * 3600 + minutes * 60 + seconds


class YouTubeClient:
    """YouTube Data API v3 client for channel video cataloging.

    Uses quota-efficient endpoints:
    - playlistItems.list: 1 quota unit per page (50 items)
    - videos.list: 1 quota unit per batch (50 video IDs)
    NOT search.list (100 units per call).
    """

    def __init__(self, api_key: str) -> None:
        from googleapiclient.discovery import build

        self._youtube = build("youtube", "v3", developerKey=api_key)
        self._quota_used = 0

    @property
    def quota_used(self) -> int:
        """Total quota units consumed by this client instance."""
        return self._quota_used

    def get_uploads_playlist_id(self, channel_id: str) -> str:
        """Convert channel ID to uploads playlist ID.

        Shortcut: replace 'UC' prefix with 'UU'. Costs 0 quota units.
        Fallback: channels.list API call (3 quota units) if prefix doesn't match.

        Args:
            channel_id: YouTube channel ID (e.g., "UCabc123").

        Returns:
            Uploads playlist ID (e.g., "UUabc123").

        Raises:
            ValueError: If channel not found via API fallback.
        """
        if channel_id.startswith("UC"):
            return "UU" + channel_id[2:]

        # Fallback: channels.list API call (3 quota units)
        self._quota_used += 3
        response = self._youtube.channels().list(part="contentDetails", id=channel_id).execute()
        items = response.get("items", [])
        if not items:
            raise ValueError(f"Channel not found: {channel_id}")
        return items[0]["contentDetails"]["relatedPlaylists"]["uploads"]

    def list_playlist_videos(self, playlist_id: str, page_token: str | None = None) -> dict:
        """Fetch a page of videos from a playlist.

        Uses playlistItems.list -- 1 quota unit per call.

        Args:
            playlist_id: YouTube playlist ID.
            page_token: Pagination token for next page.

        Returns:
            Raw API response dict with 'items' and optional 'nextPageToken'.
        """
        self._quota_used += 1
        request = self._youtube.playlistItems().list(
            part="snippet",
            playlistId=playlist_id,
            maxResults=50,
            pageToken=page_token,
        )
        return request.execute()

    def get_video_details(self, video_ids: list[str]) -> list[dict]:
        """Fetch video details (duration, description, categoryId) in batches of 50.

        Uses videos.list -- 1 quota unit per call.

        Args:
            video_ids: List of YouTube video IDs (max 50 per batch).

        Returns:
            List of video detail dicts from the API.
        """
        results: list[dict] = []
        # Process in batches of 50
        for i in range(0, len(video_ids), 50):
            batch = video_ids[i : i + 50]
            self._quota_used += 1
            request = self._youtube.videos().list(
                part="snippet,contentDetails",
                id=",".join(batch),
            )
            response = request.execute()
            results.extend(response.get("items", []))
        return results

    def fetch_all_channel_videos(self, channel_id: str, max_pages: int = 100) -> list[dict]:
        """Fetch ALL videos from a channel with details.

        Paginates through uploads playlist, batches video detail calls.

        Args:
            channel_id: YouTube channel ID.
            max_pages: Maximum pages to fetch (default 100 = ~5000 videos).

        Returns:
            List of dicts with: video_id, title, description, published_at,
            duration_iso, duration_seconds, category_id, thumbnail_url.
        """
        playlist_id = self.get_uploads_playlist_id(channel_id)

        # Collect all video IDs and basic info from playlist
        all_playlist_items: list[dict] = []
        page_token: str | None = None
        pages_fetched = 0

        while pages_fetched < max_pages:
            response = self.list_playlist_videos(playlist_id, page_token)
            items = response.get("items", [])
            all_playlist_items.extend(items)
            pages_fetched += 1

            page_token = response.get("nextPageToken")
            if not page_token:
                break

        logger.info(
            "playlist_items_fetched",
            channel_id=channel_id,
            total_items=len(all_playlist_items),
            pages=pages_fetched,
        )

        # Extract video IDs
        video_ids = [
            item["snippet"]["resourceId"]["videoId"]
            for item in all_playlist_items
            if "snippet" in item and "resourceId" in item["snippet"]
        ]

        if not video_ids:
            return []

        # Fetch video details in batches
        video_details = self.get_video_details(video_ids)

        # Build lookup map from video details
        details_map: dict[str, dict] = {}
        for detail in video_details:
            details_map[detail["id"]] = detail

        # Merge playlist items with details
        results: list[dict] = []
        for item in all_playlist_items:
            snippet = item.get("snippet", {})
            video_id = snippet.get("resourceId", {}).get("videoId")
            if not video_id:
                continue

            detail = details_map.get(video_id, {})
            detail_snippet = detail.get("snippet", {})
            content_details = detail.get("contentDetails", {})

            duration_iso = content_details.get("duration", "")
            duration_seconds = _parse_iso_duration(duration_iso) if duration_iso else None

            # Use detail snippet for richer data, fall back to playlist snippet
            thumbnails = detail_snippet.get("thumbnails", snippet.get("thumbnails", {}))
            thumbnail_url = (
                thumbnails.get("high", {}).get("url")
                or thumbnails.get("medium", {}).get("url")
                or thumbnails.get("default", {}).get("url")
            )

            results.append(
                {
                    "video_id": video_id,
                    "title": detail_snippet.get("title", snippet.get("title", "")),
                    "description": detail_snippet.get("description", snippet.get("description", "")),
                    "published_at": snippet.get("publishedAt", ""),
                    "duration_iso": duration_iso,
                    "duration_seconds": duration_seconds,
                    "category_id": detail_snippet.get("categoryId", ""),
                    "thumbnail_url": thumbnail_url,
                }
            )

        logger.info(
            "channel_videos_fetched",
            channel_id=channel_id,
            total_videos=len(results),
            quota_used=self._quota_used,
        )

        return results
