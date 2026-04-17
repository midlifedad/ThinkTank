"""Global kill switch via workers_active system config.

Spec reference: Section 3.12 (system_config).
When workers_active is false, no worker should claim any new job.
Workers check this on every poll cycle.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from thinktank.models.config_table import SystemConfig


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

    # value is JSONB; could be wrapped dict, raw bool, or the string
    # "true"/"false" left by an operator editing via the admin UI.
    # HANDLERS-REVIEW LO-01: a raw string "false" was previously coerced
    # to True by bool() (any non-empty string is truthy), silently
    # leaving workers active when the operator tried to kill them.
    if isinstance(row, dict):
        value = row.get("value", True)
    else:
        value = row
    if isinstance(value, str):
        return value.strip().lower() not in ("false", "0", "no", "off", "")
    return bool(value)
