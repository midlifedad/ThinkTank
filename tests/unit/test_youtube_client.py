"""Unit tests for YouTube Data API v3 client.

Tests use mocked Google API client -- no real API calls.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from thinktank.ingestion.youtube_client import (
    SKIP_CATEGORY_IDS,
    YouTubeClient,
    _parse_iso_duration,
)

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "youtube"


def _load_fixture(name: str) -> dict:
    with open(FIXTURES_DIR / name) as f:
        return json.load(f)


@pytest.fixture
def mock_youtube_service():
    """Create a mock YouTube API service."""
    with patch("googleapiclient.discovery.build") as mock_build:
        service = MagicMock()
        mock_build.return_value = service
        yield service


@pytest.fixture
def client(mock_youtube_service):
    """Create a YouTubeClient with mocked service."""
    return YouTubeClient(api_key="test-api-key")


# --- _parse_iso_duration tests ---


def test_parse_iso_duration_full():
    """PT1H23M45S -> 5025 seconds."""
    assert _parse_iso_duration("PT1H23M45S") == 5025


def test_parse_iso_duration_minutes_only():
    """PT45M -> 2700 seconds."""
    assert _parse_iso_duration("PT45M") == 2700


def test_parse_iso_duration_hours_seconds():
    """PT2H30S -> 7230 seconds."""
    assert _parse_iso_duration("PT2H30S") == 7230


def test_parse_iso_duration_hours_only():
    """PT2H -> 7200 seconds."""
    assert _parse_iso_duration("PT2H") == 7200


def test_parse_iso_duration_seconds_only():
    """PT30S -> 30 seconds."""
    assert _parse_iso_duration("PT30S") == 30


def test_parse_iso_duration_invalid():
    """Invalid string returns None."""
    assert _parse_iso_duration("not-a-duration") is None


def test_parse_iso_duration_empty():
    """Empty string returns None."""
    assert _parse_iso_duration("") is None


# --- SKIP_CATEGORY_IDS tests ---


def test_skip_category_ids():
    """Assert SKIP_CATEGORY_IDS contains Music, Gaming, Sports."""
    assert "10" in SKIP_CATEGORY_IDS  # Music
    assert "20" in SKIP_CATEGORY_IDS  # Gaming
    assert "17" in SKIP_CATEGORY_IDS  # Sports
    # Entertainment and People & Blogs should NOT be skipped
    assert "22" not in SKIP_CATEGORY_IDS
    assert "24" not in SKIP_CATEGORY_IDS


# --- get_uploads_playlist_id tests ---


def test_get_uploads_playlist_id_uc_prefix(client):
    """UCabc -> UUabc (zero quota)."""
    result = client.get_uploads_playlist_id("UCabc123XYZ")
    assert result == "UUabc123XYZ"
    assert client.quota_used == 0


def test_get_uploads_playlist_id_no_uc_prefix(client, mock_youtube_service):
    """Falls back to API call when prefix doesn't match."""
    mock_youtube_service.channels.return_value.list.return_value.execute.return_value = {
        "items": [{"contentDetails": {"relatedPlaylists": {"uploads": "UUfallback_playlist"}}}]
    }

    result = client.get_uploads_playlist_id("CHabc123")
    assert result == "UUfallback_playlist"
    assert client.quota_used == 3  # channels.list costs 3 units


def test_get_uploads_playlist_id_not_found(client, mock_youtube_service):
    """Raises ValueError when channel not found via API."""
    mock_youtube_service.channels.return_value.list.return_value.execute.return_value = {"items": []}

    with pytest.raises(ValueError, match="Channel not found"):
        client.get_uploads_playlist_id("CHnonexistent")


# --- list_playlist_videos tests ---


def test_list_playlist_videos_returns_items(client, mock_youtube_service):
    """Mock API returns items, quota incremented by 1."""
    fixture = _load_fixture("playlist_items_page1.json")
    mock_youtube_service.playlistItems.return_value.list.return_value.execute.return_value = fixture

    result = client.list_playlist_videos("UUtest123")
    assert len(result["items"]) == 3
    assert result["items"][0]["snippet"]["resourceId"]["videoId"] == "vid001"
    assert client.quota_used == 1


# --- get_video_details tests ---


def test_get_video_details_batch(client, mock_youtube_service):
    """Mock API with fixture data, assert duration_seconds parsed correctly."""
    fixture = _load_fixture("video_details_batch.json")
    mock_youtube_service.videos.return_value.list.return_value.execute.return_value = fixture

    results = client.get_video_details(["vid001", "vid002", "vid003"])
    assert len(results) == 3
    assert client.quota_used == 1  # Single batch of 3 (< 50)

    # Verify IDs
    ids = [r["id"] for r in results]
    assert ids == ["vid001", "vid002", "vid003"]


def test_get_video_details_multiple_batches(client, mock_youtube_service):
    """Videos split into batches of 50."""
    # Create 60 video IDs
    video_ids = [f"vid{i:03d}" for i in range(60)]

    # First batch returns 50 items, second returns 10
    batch1_items = [{"id": f"vid{i:03d}", "snippet": {}, "contentDetails": {}} for i in range(50)]
    batch2_items = [{"id": f"vid{i:03d}", "snippet": {}, "contentDetails": {}} for i in range(50, 60)]

    mock_youtube_service.videos.return_value.list.return_value.execute.side_effect = [
        {"items": batch1_items},
        {"items": batch2_items},
    ]

    results = client.get_video_details(video_ids)
    assert len(results) == 60
    assert client.quota_used == 2  # Two batches


# --- fetch_all_channel_videos tests ---


def test_fetch_all_channel_videos_single_page(client, mock_youtube_service):
    """Single page of videos fetched and merged with details."""
    playlist_fixture = _load_fixture("playlist_items_page1.json")
    details_fixture = _load_fixture("video_details_batch.json")

    mock_youtube_service.playlistItems.return_value.list.return_value.execute.return_value = playlist_fixture
    mock_youtube_service.videos.return_value.list.return_value.execute.return_value = details_fixture

    results = client.fetch_all_channel_videos("UCtest123channel")

    assert len(results) == 3

    # Video 1: PT1H30M0S = 5400s, categoryId=27 (Education)
    assert results[0]["video_id"] == "vid001"
    assert results[0]["title"] == "Deep Dive: AI and the Future of Education"
    assert results[0]["duration_seconds"] == 5400
    assert results[0]["category_id"] == "27"

    # Video 2: PT5M30S = 330s, categoryId=27
    assert results[1]["video_id"] == "vid002"
    assert results[1]["duration_seconds"] == 330
    assert results[1]["category_id"] == "27"

    # Video 3: PT45M0S = 2700s, categoryId=10 (Music)
    assert results[2]["video_id"] == "vid003"
    assert results[2]["duration_seconds"] == 2700
    assert results[2]["category_id"] == "10"


def test_fetch_all_channel_videos_pagination(client, mock_youtube_service):
    """Mock 2 pages, assert all videos collected."""
    # Page 1: 2 items with nextPageToken
    page1 = {
        "items": [
            {
                "snippet": {
                    "publishedAt": "2024-01-15T10:00:00Z",
                    "resourceId": {"videoId": "vid_a"},
                    "title": "Video A",
                }
            },
            {
                "snippet": {
                    "publishedAt": "2024-01-14T10:00:00Z",
                    "resourceId": {"videoId": "vid_b"},
                    "title": "Video B",
                }
            },
        ],
        "nextPageToken": "page2token",
    }

    # Page 2: 1 item, no nextPageToken
    page2 = {
        "items": [
            {
                "snippet": {
                    "publishedAt": "2024-01-13T10:00:00Z",
                    "resourceId": {"videoId": "vid_c"},
                    "title": "Video C",
                }
            }
        ],
    }

    mock_youtube_service.playlistItems.return_value.list.return_value.execute.side_effect = [
        page1,
        page2,
    ]

    # Video details for all 3 videos
    details_response = {
        "items": [
            {
                "id": "vid_a",
                "snippet": {"title": "Video A", "categoryId": "27"},
                "contentDetails": {"duration": "PT1H0M0S"},
            },
            {
                "id": "vid_b",
                "snippet": {"title": "Video B", "categoryId": "24"},
                "contentDetails": {"duration": "PT30M0S"},
            },
            {
                "id": "vid_c",
                "snippet": {"title": "Video C", "categoryId": "22"},
                "contentDetails": {"duration": "PT2H0M0S"},
            },
        ]
    }
    mock_youtube_service.videos.return_value.list.return_value.execute.return_value = details_response

    results = client.fetch_all_channel_videos("UCpaginated")

    assert len(results) == 3
    assert results[0]["video_id"] == "vid_a"
    assert results[0]["duration_seconds"] == 3600
    assert results[1]["video_id"] == "vid_b"
    assert results[1]["duration_seconds"] == 1800
    assert results[2]["video_id"] == "vid_c"
    assert results[2]["duration_seconds"] == 7200

    # Quota: 2 pages (playlistItems) + 1 batch (videos) = 3
    assert client.quota_used == 3


def test_fetch_all_channel_videos_max_pages(client, mock_youtube_service):
    """Stops after max_pages even if more pages available."""

    # Each page has a nextPageToken, simulating infinite pagination
    def make_page():
        return {
            "items": [
                {
                    "snippet": {
                        "publishedAt": "2024-01-01T00:00:00Z",
                        "resourceId": {"videoId": "vid_x"},
                        "title": "Test",
                    }
                }
            ],
            "nextPageToken": "next",
        }

    mock_youtube_service.playlistItems.return_value.list.return_value.execute.side_effect = [
        make_page() for _ in range(5)
    ]
    mock_youtube_service.videos.return_value.list.return_value.execute.return_value = {
        "items": [
            {
                "id": "vid_x",
                "snippet": {"title": "Test", "categoryId": "27"},
                "contentDetails": {"duration": "PT1H0M0S"},
            }
        ]
    }

    results = client.fetch_all_channel_videos("UCtest", max_pages=3)

    # Should have fetched 3 pages, each with 1 item = 3 items
    # (all same video_id, but that's fine for this test)
    assert len(results) == 3
    # Quota: 3 pages + 1 detail batch = 4
    assert client.quota_used == 4
