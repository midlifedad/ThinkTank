"""Health check endpoint for ThinkTank API."""

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from thinktank.api.dependencies import get_session
from thinktank.config import get_settings
from thinktank.logging import get_logger

router = APIRouter()


@router.get("/health", response_model=None)
async def health_check(session: AsyncSession = Depends(get_session)) -> dict | JSONResponse:
    """Check database connectivity and return service health status.

    Returns 200 with {status: healthy} when DB is connected.
    Returns 503 with {status: unhealthy} when DB is unreachable.
    """
    settings = get_settings()
    logger = get_logger("thinktank.api.health")

    try:
        await session.execute(text("SELECT 1"))
        logger.debug("Health check passed", status="healthy")
        return {"status": "healthy", "service": settings.service_name}
    except Exception:
        logger.error("Health check failed", status="unhealthy")
        return JSONResponse(
            status_code=503,
            content={"status": "unhealthy", "service": settings.service_name},
        )
