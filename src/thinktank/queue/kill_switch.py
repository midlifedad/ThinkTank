"""Global kill switch via workers_active system config.

Spec reference: Section 3.12 (system_config).
When workers_active is false, no worker should claim any new job.
Workers check this on every poll cycle.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.thinktank.models.config_table import SystemConfig


async def is_workers_active(session: AsyncSession) -> bool:
    """Check the global kill switch from system_config.

    Returns True if workers should continue claiming jobs,
    False if all job claiming should halt.

    Fail-open: if no workers_active key exists, returns True
    (workers are assumed active by default).

    Handles JSONB value formats:
    - Raw boolean: true / false
    - Wrapped dict: {"value": true} / {"value": false}

    Args:
        session: Async database session.

    Returns:
        True if workers are active, False if killed.
    """
    stmt = select(SystemConfig.value).where(SystemConfig.key == "workers_active")
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()

    if row is None:
        # No config entry = default to active (fail-open)
        return True

    # value is JSONB, could be {"value": true/false} or just true/false
    if isinstance(row, dict):
        return bool(row.get("value", True))
    return bool(row)
