"""ThinkTank FastAPI application with async lifespan."""

import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from thinktank.api.health import router as health_router
from thinktank.database import engine


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Manage application lifecycle: verify DB on startup, dispose on shutdown."""
    # Startup: verify database connection
    async with engine.begin() as conn:
        await conn.execute(text("SELECT 1"))
    yield
    # Shutdown: dispose engine
    await engine.dispose()


app = FastAPI(
    title="ThinkTank",
    version="0.1.0",
    description="Global intelligence ingestion platform",
    lifespan=lifespan,
)

# CORS middleware - configurable origins, default to ["*"] for development
cors_origins = os.getenv("CORS_ORIGINS", '["*"]')
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(health_router)
