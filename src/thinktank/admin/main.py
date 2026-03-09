"""ThinkTank Admin FastAPI application with async lifespan.

Separate from the API application -- serves the HTMX-powered admin dashboard
for human oversight of the pipeline.
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import text

from thinktank.admin.routers.dashboard import router as dashboard_router
from thinktank.api.middleware import CorrelationIDMiddleware
from thinktank.config import get_settings
from thinktank.database import engine
from thinktank.logging import configure_logging, get_logger


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Manage application lifecycle: configure logging, verify DB, dispose on shutdown."""
    settings = get_settings()

    # Startup: configure structured logging
    configure_logging("thinktank-admin", settings.log_level)
    logger = get_logger("thinktank.admin")
    logger.info("ThinkTank Admin starting", service="thinktank-admin")

    # Startup: verify database connection
    async with engine.begin() as conn:
        await conn.execute(text("SELECT 1"))
    logger.info("Database connection verified")

    yield

    # Shutdown
    logger = get_logger("thinktank.admin")
    logger.info("ThinkTank Admin shutting down")
    await engine.dispose()


app = FastAPI(
    title="ThinkTank Admin",
    version="0.1.0",
    description="Admin dashboard for ThinkTank pipeline oversight",
    lifespan=lifespan,
)

# Correlation ID middleware
app.add_middleware(CorrelationIDMiddleware, service_name="thinktank-admin")

# Include routers -- only dashboard for now; llm_panel and categories added in Task 2
app.include_router(dashboard_router)
