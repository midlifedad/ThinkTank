"""Handler: rollup_api_usage -- Aggregate rate_limit_usage into api_usage hourly rollups.

Aggregates raw rate_limit_usage rows (older than the current hour) into
api_usage rows with call counts and estimated costs. Purges raw rows
older than 2 hours after aggregation.

Uses a NOT EXISTS subquery check for idempotency (no duplicate rollups
for the same api_name + period_start).

Spec reference: Section 7 (Operations / API cost tracking).
"""

from __future__ import annotations

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from thinktank.config import get_settings
from thinktank.models.job import Job

logger = structlog.get_logger(__name__)

# Cost per API call in USD. Unknown APIs default to $0.001.
# Anthropic is deliberately NOT in this map (A2): LLM calls never pass
# through rate_limit_usage -- their cost is aggregated token-based from
# llm_reviews below, using the per-mtoken rates in Settings.
API_COST_MAP: dict[str, float] = {
    "youtube": 0.001,
    "podcastindex": 0.0,
}

# Default cost for APIs not in the map
_DEFAULT_COST = 0.001


async def handle_rollup_api_usage(session: AsyncSession, job: Job) -> None:
    """Aggregate rate_limit_usage into api_usage with cost estimates.

    Steps:
    1. Aggregate rate_limit_usage rows older than current hour into hourly buckets
    2. Insert into api_usage where not already present (idempotent)
    3. Purge rate_limit_usage rows older than 2 hours

    Args:
        session: Active database session.
        job: The rollup_api_usage job.
    """
    logger.info("rollup_api_usage: starting aggregation", job_id=str(job.id))

    # Step 1 & 2: Aggregate and insert missing hourly rollups.
    # Uses a CTE to first aggregate, then filter out already-rolled-up periods.
    # NOW() (TIMESTAMPTZ) -- called_at is TIMESTAMPTZ, so LOCALTIMESTAMP
    # only worked while the session timezone happened to be UTC (A2).
    aggregation_sql = text("""
        WITH agg AS (
            SELECT
                r.api_name,
                date_trunc('hour', r.called_at) AS period,
                COUNT(*) AS cnt
            FROM rate_limit_usage r
            WHERE r.called_at < date_trunc('hour', NOW())
            GROUP BY r.api_name, date_trunc('hour', r.called_at)
        )
        INSERT INTO api_usage (id, api_name, endpoint, period_start, call_count, estimated_cost_usd)
        SELECT
            gen_random_uuid(),
            agg.api_name,
            'rollup',
            agg.period,
            agg.cnt,
            NULL
        FROM agg
        WHERE NOT EXISTS (
            SELECT 1 FROM api_usage a
            WHERE a.api_name = agg.api_name
              AND a.period_start = agg.period
              AND a.endpoint = 'rollup'
        )
    """)
    result = await session.execute(aggregation_sql)
    inserted_count = result.rowcount
    logger.info("rollup_api_usage: inserted rollup rows", count=inserted_count)

    # Step 2a (A2): Aggregate Anthropic usage token-based from llm_reviews.
    # LLM calls never touch rate_limit_usage, so before this step the cost
    # dashboard was blind to LLM spend entirely. Rows with the input/output
    # split (migration 015) are priced at the configured per-mtoken rates;
    # pre-015 rows only have the combined tokens_used and are priced at the
    # blended midpoint. llm_reviews is an audit trail -- never purged here.
    settings = get_settings()
    in_rate = settings.llm_input_cost_per_mtok
    out_rate = settings.llm_output_cost_per_mtok
    blended_rate = (in_rate + out_rate) / 2

    anthropic_sql = text("""
        WITH agg AS (
            SELECT
                date_trunc('hour', l.created_at) AS period,
                COUNT(*) AS cnt,
                COALESCE(SUM(l.tokens_used), 0) AS total_tokens,
                COALESCE(SUM(l.input_tokens), 0) AS in_tokens,
                COALESCE(SUM(l.output_tokens), 0) AS out_tokens,
                COALESCE(SUM(
                    CASE WHEN l.input_tokens IS NULL AND l.output_tokens IS NULL
                         THEN l.tokens_used ELSE 0 END
                ), 0) AS legacy_tokens
            FROM llm_reviews l
            WHERE l.created_at < date_trunc('hour', NOW())
            GROUP BY date_trunc('hour', l.created_at)
        )
        INSERT INTO api_usage (id, api_name, endpoint, period_start, call_count, units_consumed, estimated_cost_usd)
        SELECT
            gen_random_uuid(),
            'anthropic',
            'llm_review',
            agg.period,
            agg.cnt,
            agg.total_tokens,
            (agg.in_tokens * :in_rate + agg.out_tokens * :out_rate + agg.legacy_tokens * :blended_rate) / 1000000.0
        FROM agg
        WHERE NOT EXISTS (
            SELECT 1 FROM api_usage a
            WHERE a.api_name = 'anthropic'
              AND a.period_start = agg.period
              AND a.endpoint = 'llm_review'
        )
    """)
    anthropic_result = await session.execute(
        anthropic_sql,
        {"in_rate": in_rate, "out_rate": out_rate, "blended_rate": blended_rate},
    )
    logger.info("rollup_api_usage: inserted anthropic rollup rows", count=anthropic_result.rowcount)

    # Step 2b: Apply cost estimates to newly inserted rows that have NULL cost.
    # We do this in Python by querying and updating, since cost map is in-app.
    from sqlalchemy import select as sa_select

    from thinktank.models.api_usage import ApiUsage

    uncost_result = await session.execute(
        sa_select(ApiUsage).where(
            ApiUsage.endpoint == "rollup",
            ApiUsage.estimated_cost_usd.is_(None),
        )
    )
    uncost_rows = uncost_result.scalars().all()
    for row in uncost_rows:
        cost_per_call = API_COST_MAP.get(row.api_name, _DEFAULT_COST)
        row.estimated_cost_usd = row.call_count * cost_per_call

    await session.flush()

    # Step 3: Purge old rate_limit_usage rows (> 2 hours old).
    purge_sql = text("""
        DELETE FROM rate_limit_usage
        WHERE called_at < NOW() - INTERVAL '2 hours'
    """)
    purge_result = await session.execute(purge_sql)
    logger.info("rollup_api_usage: purged old rows", count=purge_result.rowcount)

    # Persist all writes. The worker loop wraps us in `async with
    # session_factory() as session:` without auto-commit, so we MUST
    # commit here or every rollup insert + purge silently rolls back
    # on session close.
    await session.commit()
