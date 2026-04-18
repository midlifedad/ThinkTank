"""CSRF protection middleware for the admin app.

Source: ADMIN-REVIEW HI-05.

Double-submit cookie pattern:

* On any safe (GET/HEAD/OPTIONS) response to ``/admin/*``, the middleware
  ensures a ``csrf_token`` cookie is set. The cookie is NOT HttpOnly so
  HTMX JavaScript can read it and echo it back in an ``X-CSRF-Token``
  header on every subsequent request.
* On any state-changing (POST/PUT/PATCH/DELETE) request to ``/admin/*``,
  the middleware compares the cookie value to the header. If they don't
  match (constant-time compare) the request is rejected with 403.

``/admin/login`` is exempt because clients can't acquire a token before
they authenticate. Every other admin mutation, including ``/admin/logout``,
must carry the header.

HTMX picks the header up via a small snippet in ``base.html`` that hooks
``htmx:configRequest``.
"""

from __future__ import annotations

import hmac
import secrets

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

CSRF_COOKIE = "csrf_token"
CSRF_HEADER = "X-CSRF-Token"
_SAFE_METHODS = frozenset(("GET", "HEAD", "OPTIONS"))
_ADMIN_PREFIX = "/admin"
_EXEMPT_PATHS = frozenset(("/admin/login",))
_TOKEN_BYTES = 32


def generate_csrf_token() -> str:
    """Return a URL-safe random token suitable for the CSRF cookie."""
    return secrets.token_urlsafe(_TOKEN_BYTES)


class CSRFMiddleware(BaseHTTPMiddleware):
    """Enforce double-submit-cookie CSRF on admin mutations."""

    def __init__(self, app: object) -> None:
        super().__init__(app)  # type: ignore[arg-type]

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path
        if not path.startswith(_ADMIN_PREFIX):
            return await call_next(request)

        if request.method in _SAFE_METHODS:
            response = await call_next(request)
            if not request.cookies.get(CSRF_COOKIE):
                response.set_cookie(
                    key=CSRF_COOKIE,
                    value=generate_csrf_token(),
                    httponly=False,
                    samesite="lax",
                    secure=request.url.scheme == "https",
                    path="/",
                )
            return response

        if path in _EXEMPT_PATHS:
            return await call_next(request)

        cookie_token = request.cookies.get(CSRF_COOKIE)
        header_token = request.headers.get(CSRF_HEADER)
        if not cookie_token or not header_token or not hmac.compare_digest(cookie_token, header_token):
            return JSONResponse({"detail": "CSRF validation failed"}, status_code=403)

        return await call_next(request)
