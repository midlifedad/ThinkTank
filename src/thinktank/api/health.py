"""Health check endpoint for ThinkTank API."""

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from thinktank.api.dependencies import get_session

router = APIRouter()


@router.get("/health", response_model=None)
async def health_check(session: AsyncSession = Depends(get_session)) -> dict | JSONResponse:
    """Check database connectivity and return service health status.

    Returns 200 with {status: healthy} when DB is connected.
    Returns 503 with {status: unhealthy} when DB is unreachable.
    """
    try:
        await session.execute(text("SELECT 1"))
        return {"status": "healthy", "service": "thinktank-api"}
    except Exception:
        return JSONResponse(
            status_code=503,
            content={"status": "unhealthy", "service": "thinktank-api"},
        )
