"""ThinkTank Admin FastAPI application with async lifespan.

Separate from the API application -- serves the HTMX-powered admin dashboard
for human oversight of the pipeline.
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from sqlalchemy import text

from thinktank.admin.auth import require_admin
from thinktank.admin.csrf import CSRFMiddleware
from thinktank.admin.routers.api_keys import router as api_keys_router
from thinktank.admin.routers.auth import router as auth_router
from thinktank.admin.routers.categories import router as categories_router
from thinktank.admin.routers.chat import router as chat_router
from thinktank.admin.routers.config import router as config_router
from thinktank.admin.routers.dashboard import router as dashboard_router
from thinktank.admin.routers.llm_panel import router as llm_panel_router
from thinktank.admin.routers.pipeline import router as pipeline_router
from thinktank.admin.routers.sources import router as sources_router
from thinktank.admin.routers.thinkers import router as thinkers_router
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

# CSRF protection on state-changing admin endpoints (ADMIN-REVIEW HI-05).
app.add_middleware(CSRFMiddleware)

# Login / logout: publicly reachable (no require_admin) so humans can
# obtain a cookie. All other admin routes are gated behind require_admin.
# See ADMIN-REVIEW CR-01.
app.include_router(auth_router)

_admin_auth = [Depends(require_admin)]
app.include_router(dashboard_router, dependencies=_admin_auth)
app.include_router(llm_panel_router, dependencies=_admin_auth)
app.include_router(categories_router, dependencies=_admin_auth)
app.include_router(api_keys_router, dependencies=_admin_auth)
app.include_router(config_router, dependencies=_admin_auth)
app.include_router(thinkers_router, dependencies=_admin_auth)
app.include_router(sources_router, dependencies=_admin_auth)
app.include_router(pipeline_router, dependencies=_admin_auth)
app.include_router(chat_router, dependencies=_admin_auth)
