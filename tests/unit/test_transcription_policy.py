"""Unit tests for the transcription age policy.

Amir directive 2026-07-11: nothing older than 5 years transcribes, to
start. Policy is runtime-tunable via system_config
transcription_max_age_days (absent -> 5 years, 0 -> unlimited, N -> days).
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from thinktank.transcription.policy import (
    DEFAULT_MAX_AGE_DAYS,
    get_transcription_age_cutoff,
    is_transcribable,
)

NOW = datetime.now(UTC)


def _session_with_config_value(value):
    session = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=value)
    session.execute = AsyncMock(return_value=result)
    return session


class TestGetCutoff:
    @pytest.mark.asyncio
    async def test_absent_config_defaults_to_five_years(self):
        cutoff = await get_transcription_age_cutoff(_session_with_config_value(None))
        assert cutoff is not None
        # Compare against a CALL-TIME now: the module-level NOW is stamped
        # at import, and slow CI collection put >60s between import and
        # execution, flaking the original assertion (Troy, CI on #66).
        expected = datetime.now(UTC) - timedelta(days=DEFAULT_MAX_AGE_DAYS)
        assert abs((cutoff - expected).total_seconds()) < 60

    @pytest.mark.asyncio
    async def test_zero_means_unlimited(self):
        assert await get_transcription_age_cutoff(_session_with_config_value(0)) is None

    @pytest.mark.asyncio
    async def test_explicit_days(self):
        cutoff = await get_transcription_age_cutoff(_session_with_config_value(30))
        assert cutoff is not None
        assert abs((cutoff - (datetime.now(UTC) - timedelta(days=30))).total_seconds()) < 60

    @pytest.mark.asyncio
    async def test_wrapped_dict_shape(self):
        assert await get_transcription_age_cutoff(_session_with_config_value({"value": 0})) is None

    @pytest.mark.asyncio
    async def test_invalid_value_falls_back_to_default(self):
        cutoff = await get_transcription_age_cutoff(_session_with_config_value("garbage"))
        assert cutoff is not None  # default applied, not unlimited


class TestIsTranscribable:
    def test_unlimited_cutoff_accepts_anything(self):
        assert is_transcribable(NOW - timedelta(days=10_000), None) is True

    def test_null_published_at_passes(self):
        """Missing date is a parse artifact, not evidence of age."""
        assert is_transcribable(None, NOW - timedelta(days=1825)) is True

    def test_old_episode_rejected(self):
        cutoff = NOW - timedelta(days=1825)
        assert is_transcribable(NOW - timedelta(days=2000), cutoff) is False

    def test_recent_episode_accepted(self):
        cutoff = NOW - timedelta(days=1825)
        assert is_transcribable(NOW - timedelta(days=30), cutoff) is True

    def test_naive_datetime_assumed_utc(self):
        cutoff = NOW - timedelta(days=1825)
        naive_recent = (NOW - timedelta(days=30)).replace(tzinfo=None)
        assert is_transcribable(naive_recent, cutoff) is True
