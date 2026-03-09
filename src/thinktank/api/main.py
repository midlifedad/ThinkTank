"""ThinkTank FastAPI application with async lifespan."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from thinktank.api.health import router as health_router
from thinktank.api.routers.config import router as config_router
from thinktank.api.routers.content import router as content_router
from thinktank.api.routers.jobs import router as jobs_router
from thinktank.api.routers.sources import router as sources_router
from thinktank.api.routers.thinkers import router as thinkers_router
from thinktank.api.middleware import CorrelationIDMiddleware
from thinktank.config import get_settings
from thinktank.database import engine
from thinktank.logging import configure_logging, get_logger


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Manage application lifecycle: configure logging, verify DB, dispose on shutdown."""
    settings = get_settings()

    # Startup: configure structured logging
    configure_logging(settings.service_name, settings.log_level)
    logger = get_logger("thinktank.api")
    logger.info("ThinkTank API starting", service=settings.service_name)

    # Startup: verify database connection
    async with engine.begin() as conn:
        await conn.execute(text("SELECT 1"))
    logger.info("Database connection verified")

    yield

    # Shutdown
    logger = get_logger("thinktank.api")
    logger.info("ThinkTank API shutting down")
    await engine.dispose()


app = FastAPI(
    title="ThinkTank",
    version="0.1.0",
    description="Global intelligence ingestion platform",
    lifespan=lifespan,
)

# Correlation ID middleware - must be added before CORS
settings = get_settings()
app.add_middleware(CorrelationIDMiddleware, service_name=settings.service_name)

# CORS middleware - configurable origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(health_router)
app.include_router(thinkers_router)
app.include_router(sources_router)
app.include_router(content_router)
app.include_router(jobs_router)
app.include_router(config_router)
