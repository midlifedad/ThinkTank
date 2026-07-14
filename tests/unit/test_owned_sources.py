"""Unit tests for owned-channel discovery (W3.1): fail-open + cost."""

from unittest.mock import AsyncMock, patch

import pytest

from thinktank.discovery.exa_client import ExaResult
from thinktank.discovery.owned_sources import OwnedChannels, find_owned_channels
from thinktank.llm.client import LLMUsage

pytestmark = pytest.mark.anyio


def _usage():
    return LLMUsage(input_tokens=300, output_tokens=100)


def _exa(url):
    return ExaResult(url=url, title="t", text=None, published_at=None, author=None)


class TestFindOwnedChannels:
    async def test_returns_channels_and_records_cost(self, session):
        from sqlalchemy import func, select

        from thinktank.models.api_usage import ApiUsage

        verdict = OwnedChannels(
            youtube_channel_url="https://youtube.com/@drtest",
            substack_url=None,
            podcast_url=None,
            website_url="https://drtest.com",
            reasoning="Channel name and bio match.",
        )
        cost_q = select(func.count()).select_from(ApiUsage).where(ApiUsage.endpoint == "owned_source_discovery")
        before = await session.scalar(cost_q)
        with (
            patch(
                "thinktank.discovery.owned_sources.exa_search",
                new=AsyncMock(return_value=[_exa("https://youtube.com/@drtest")]),
            ),
            patch(
                "thinktank.discovery.owned_sources._client.review",
                new=AsyncMock(return_value=(verdict, _usage(), 10)),
            ),
        ):
            result = await find_owned_channels(session, "Dr. Test", "longevity")
        assert result.youtube_channel_url == "https://youtube.com/@drtest"
        assert result.website_url == "https://drtest.com"
        assert await session.scalar(cost_q) == before + 1

    async def test_no_exa_results_returns_none(self, session):
        with patch("thinktank.discovery.owned_sources.exa_search", new=AsyncMock(return_value=[])):
            assert await find_owned_channels(session, "Nobody") is None

    async def test_llm_failure_is_fail_open(self, session):
        with (
            patch("thinktank.discovery.owned_sources.exa_search", new=AsyncMock(return_value=[_exa("https://x.com")])),
            patch("thinktank.discovery.owned_sources._client.review", new=AsyncMock(side_effect=RuntimeError("boom"))),
        ):
            assert await find_owned_channels(session, "Dr. Test") is None
