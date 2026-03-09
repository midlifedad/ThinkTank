"""ThinkTank FastAPI application with async lifespan."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from thinktank.api.health import router as health_router
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
