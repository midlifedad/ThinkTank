"""Railway GraphQL client for GPU service scaling.

Spec reference: Section 6.5 (GPU Worker Orchestration).
Uses Railway GraphQL API at backboard.railway.com/graphql/v2 to
scale the GPU worker service up and down based on queue depth.
"""

from datetime import UTC, datetime, timedelta

import httpx
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from thinktank.ingestion.config_reader import get_config_value
from thinktank.queue.backpressure import get_queue_depth
from thinktank.secrets import get_secret

logger = structlog.get_logger(__name__)

RAILWAY_API_URL = "https://backboard.railway.com/graphql/v2"


async def scale_gpu_service(replicas: int, session: AsyncSession | None = None) -> bool:
    """Scale the GPU worker service to the specified replica count.

    Uses Railway GraphQL API with serviceInstanceUpdate mutation.

    Args:
        replicas: Number of replicas (0 to scale down, 1+ to scale up).
        session: Database session for secret lookup (falls back to env vars).

    Returns:
        True if successful, False on error or missing config.
    """
    if session:
        api_key = await get_secret(session, "railway_api_key")
        service_id = await get_secret(session, "railway_gpu_service_id")
        environment_id = await get_secret(session, "railway_environment_id")
    else:
        import os
        api_key = os.environ.get("RAILWAY_API_KEY")
        service_id = os.environ.get("RAILWAY_GPU_SERVICE_ID")
        environment_id = os.environ.get("RAILWAY_ENVIRONMENT_ID")

    if not all([api_key, service_id, environment_id]):
        logger.warning(
            "railway_config_missing",
            has_api_key=bool(api_key),
            has_service_id=bool(service_id),
            has_environment_id=bool(environment_id),
        )
        return False

    mutation = """
    mutation($serviceId: String!, $environmentId: String!, $input: ServiceInstanceUpdateInput!) {
        serviceInstanceUpdate(
            serviceId: $serviceId,
            environmentId: $environmentId,
            input: $input
        )
    }
    """

    variables = {
        "serviceId": service_id,
        "environmentId": environment_id,
        "input": {"numReplicas": replicas},
    }

    try:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            resp = await client.post(
                RAILWAY_API_URL,
                json={"query": mutation, "variables": variables},
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=30.0,
            )
            resp.raise_for_status()
            data = resp.json()

            if "errors" in data:
                logger.error(
                    "railway_api_errors",
                    errors=data["errors"],
                    replicas=replicas,
                )
                return False

            logger.info(
                "gpu_service_scaled",
                replicas=replicas,
            )
            return True

    except Exception:
        logger.error(
            "railway_api_failed",
            replicas=replicas,
            exc_info=True,
        )
        return False


async def get_gpu_replica_count(session: AsyncSession | None = None) -> int | None:
    """Get current GPU service replica count from Railway API.

    Args:
        session: Database session for secret lookup (falls back to env vars).

    Returns:
        Number of replicas, or None on error.
    """
    if session:
        api_key = await get_secret(session, "railway_api_key")
        service_id = await get_secret(session, "railway_gpu_service_id")
        environment_id = await get_secret(session, "railway_environment_id")
    else:
        import os
        api_key = os.environ.get("RAILWAY_API_KEY")
        service_id = os.environ.get("RAILWAY_GPU_SERVICE_ID")
        environment_id = os.environ.get("RAILWAY_ENVIRONMENT_ID")

    if not all([api_key, service_id, environment_id]):
        return None

    query = """
    query($serviceId: String!, $environmentId: String!) {
        serviceInstance(
            serviceId: $serviceId,
            environmentId: $environmentId
        ) {
            numReplicas
        }
    }
    """

    variables = {
        "serviceId": service_id,
        "environmentId": environment_id,
    }

    try:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            resp = await client.post(
                RAILWAY_API_URL,
                json={"query": query, "variables": variables},
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=30.0,
            )
            resp.raise_for_status()
            data = resp.json()

            if "errors" in data:
                logger.error("railway_query_errors", errors=data["errors"])
                return None

            return data["data"]["serviceInstance"]["numReplicas"]

    except Exception:
        logger.error("railway_query_failed", exc_info=True)
        return None


async def manage_gpu_scaling(
    session: AsyncSession,
    gpu_idle_since: datetime | None,
) -> tuple[bool, datetime | None]:
    """Manage GPU scaling based on queue depth and idle time.

    Logic:
    - If queue depth > threshold AND replicas == 0: scale up
    - If queue depth == 0 AND idle_since is None: start idle timer
    - If queue depth == 0 AND idle elapsed > timeout: scale down
    - If queue depth > 0: reset idle timer

    Args:
        session: Database session for reading queue depth and config.
        gpu_idle_since: When the GPU became idle (None if not idle).

    Returns:
        Tuple of (scaled: bool, new_idle_since: datetime | None).
    """
    depth = await get_queue_depth(session, "process_content")
    threshold = await get_config_value(session, "gpu_queue_threshold", 5)
    idle_minutes = await get_config_value(
        session, "gpu_idle_minutes_before_shutdown", 30
    )
    current_replicas = await get_gpu_replica_count(session)

    logger.info(
        "gpu_scaling_check",
        queue_depth=depth,
        threshold=threshold,
        idle_minutes=idle_minutes,
        current_replicas=current_replicas,
        gpu_idle_since=str(gpu_idle_since) if gpu_idle_since else None,
    )

    # Scale up: queue has work and GPU is off
    if depth > threshold and (current_replicas is None or current_replicas == 0):
        await scale_gpu_service(1, session)
        return (True, None)

    # Queue has work: reset idle timer
    if depth > 0:
        return (False, None)

    # Queue is empty: manage idle timeout
    if gpu_idle_since is None:
        # Start idle timer (timezone-naive to match project convention)
        return (False, datetime.now(UTC).replace(tzinfo=None))

    # Check if idle long enough to scale down
    elapsed = datetime.now(UTC).replace(tzinfo=None) - gpu_idle_since
    if elapsed > timedelta(minutes=idle_minutes):
        await scale_gpu_service(0, session)
        return (True, None)

    # Still within idle timeout
    return (False, gpu_idle_since)
