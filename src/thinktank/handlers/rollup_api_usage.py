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

from src.thinktank.models.job import Job

logger = structlog.get_logger(__name__)

# Cost per API call in USD. Unknown APIs default to $0.001.
API_COST_MAP: dict[str, float] = {
    "youtube": 0.001,
    "podcastindex": 0.0,
    "anthropic": 0.015,
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
    # LOCALTIMESTAMP for timezone-naive consistency.
    aggregation_sql = text("""
        WITH agg AS (
            SELECT
                r.api_name,
                date_trunc('hour', r.called_at) AS period,
                COUNT(*) AS cnt
            FROM rate_limit_usage r
            WHERE r.called_at < date_trunc('hour', LOCALTIMESTAMP)
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

    # Step 2b: Apply cost estimates to newly inserted rows that have NULL cost.
    # We do this in Python by querying and updating, since cost map is in-app.
    from sqlalchemy import select as sa_select

    from src.thinktank.models.api_usage import ApiUsage

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
        WHERE called_at < LOCALTIMESTAMP - INTERVAL '2 hours'
    """)
    purge_result = await session.execute(purge_sql)
    logger.info("rollup_api_usage: purged old rows", count=purge_result.rowcount)

    # Persist all writes. The worker loop wraps us in `async with
    # session_factory() as session:` without auto-commit, so we MUST
    # commit here or every rollup insert + purge silently rolls back
    # on session close.
    await session.commit()
