"""Contract tests for token-based Anthropic cost aggregation in rollup_api_usage.

Source: ARCH-REVIEW 2026-05-28 (A2). LLM calls never pass through
rate_limit_usage, so before A2 the cost dashboard was blind to Anthropic
spend. The rollup now aggregates llm_reviews per hour into api_usage rows
priced from real token counts.

Contract:
    - Given llm_reviews rows in completed hours
    - When rollup_api_usage runs
    - Then one api_usage row per hour (api_name='anthropic',
      endpoint='llm_review') with token-based cost; split rows priced at
      configured rates, legacy rows at the blended midpoint; idempotent;
      llm_reviews never purged
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.factories import create_job, create_llm_review
from thinktank.config import get_settings
from thinktank.handlers.rollup_api_usage import handle_rollup_api_usage
from thinktank.models.api_usage import ApiUsage
from thinktank.models.review import LLMReview

pytestmark = pytest.mark.anyio


def _past_hour(hours_ago: int = 2) -> datetime:
    """A timestamp safely inside a completed hour bucket."""
    return (datetime.now(UTC) - timedelta(hours=hours_ago)).replace(minute=30, second=0, microsecond=0)


async def _rollup(session: AsyncSession) -> None:
    job = await create_job(session, job_type="rollup_api_usage", payload={})
    await handle_rollup_api_usage(session, job)


async def _anthropic_rows(session: AsyncSession) -> list[ApiUsage]:
    result = await session.execute(
        select(ApiUsage).where(ApiUsage.api_name == "anthropic", ApiUsage.endpoint == "llm_review")
    )
    return list(result.scalars().all())


class TestAnthropicTokenRollup:
    async def test_split_rows_priced_at_configured_rates(self, session: AsyncSession):
        """input/output split rows use per-mtoken rates from Settings."""
        when = _past_hour()
        await create_llm_review(session, created_at=when, tokens_used=1500, input_tokens=1000, output_tokens=500)
        await create_llm_review(session, created_at=when, tokens_used=3000, input_tokens=2000, output_tokens=1000)

        await _rollup(session)

        rows = await _anthropic_rows(session)
        assert len(rows) == 1
        row = rows[0]
        assert row.call_count == 2
        assert row.units_consumed == 4500
        settings = get_settings()
        expected = (3000 * settings.llm_input_cost_per_mtok + 1500 * settings.llm_output_cost_per_mtok) / 1_000_000.0
        assert float(row.estimated_cost_usd) == pytest.approx(expected)

    async def test_legacy_rows_priced_at_blended_rate(self, session: AsyncSession):
        """Pre-015 rows (no split) are priced at the midpoint of the rates."""
        await create_llm_review(
            session, created_at=_past_hour(), tokens_used=2000, input_tokens=None, output_tokens=None
        )

        await _rollup(session)

        rows = await _anthropic_rows(session)
        assert len(rows) == 1
        settings = get_settings()
        blended = (settings.llm_input_cost_per_mtok + settings.llm_output_cost_per_mtok) / 2
        assert float(rows[0].estimated_cost_usd) == pytest.approx(2000 * blended / 1_000_000.0)

    async def test_current_hour_not_aggregated(self, session: AsyncSession):
        """Reviews in the still-open hour wait for the next rollup."""
        await create_llm_review(
            session, created_at=datetime.now(UTC), tokens_used=100, input_tokens=60, output_tokens=40
        )

        await _rollup(session)

        assert await _anthropic_rows(session) == []

    async def test_idempotent_across_runs(self, session: AsyncSession):
        """Running the rollup twice never duplicates a period row."""
        await create_llm_review(session, created_at=_past_hour(), tokens_used=1000, input_tokens=700, output_tokens=300)

        await _rollup(session)
        await _rollup(session)

        assert len(await _anthropic_rows(session)) == 1

    async def test_llm_reviews_never_purged(self, session: AsyncSession):
        """The audit trail survives aggregation (unlike rate_limit_usage)."""
        await create_llm_review(session, created_at=_past_hour(), tokens_used=1000, input_tokens=700, output_tokens=300)

        await _rollup(session)

        count = (await session.execute(select(func.count()).select_from(LLMReview))).scalar_one()
        assert count == 1
