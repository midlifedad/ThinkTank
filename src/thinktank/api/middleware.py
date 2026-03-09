"""Correlation ID middleware for request tracking.

Every incoming request gets a unique correlation ID that propagates
through structlog contextvars for the duration of the request.
The ID is also returned as an X-Correlation-ID response header.
"""

import uuid

import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response


class CorrelationIDMiddleware(BaseHTTPMiddleware):
    """Middleware that assigns a correlation ID to each request.

    1. Clears contextvars to prevent leaking between requests
    2. Generates a UUID correlation_id
    3. Binds correlation_id and service to structlog contextvars
    4. Processes the request
    5. Adds X-Correlation-ID response header
    """

    def __init__(self, app: object, service_name: str = "thinktank-api") -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self.service_name = service_name

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        """Process request with correlation ID tracking."""
        # CRITICAL: clear contextvars to prevent leaking between requests
        structlog.contextvars.clear_contextvars()

        # Generate and bind correlation ID
        correlation_id = str(uuid.uuid4())
        structlog.contextvars.bind_contextvars(
            correlation_id=correlation_id,
            service=self.service_name,
        )

        # Process request
        response = await call_next(request)

        # Add correlation ID to response headers
        response.headers["X-Correlation-ID"] = correlation_id

        return response
