"""Authentication for the ThinkTank admin app.

Source: ADMIN-REVIEW CR-01.

Admin routes were previously anonymous. This module provides a FastAPI
dependency ``require_admin`` that accepts either:

1. An ``Authorization: Bearer <token>`` header, or
2. An ``admin_session`` cookie.

Both are compared (constant-time) against the admin API token stored in
``system_config`` under key ``secret_admin_api_token``. That row is read
via :func:`thinktank.secrets.get_secret` so rotation via the admin UI
takes effect without a restart.

Behaviour:

* Missing/wrong credentials → ``401 Unauthorized``.
* ``secret_admin_api_token`` not configured at all → ``500 Internal Server
  Error`` with an explicit message. This fails closed: if a deployment
  forgets to set the token, the admin app stays locked rather than
  silently becoming open. The one exception is the public login endpoint
  (see :mod:`thinktank.admin.routers.auth`) which must remain reachable
  so humans can set the cookie.

CSRF protection is deliberately NOT included here — it is tracked as
ADMIN-REVIEW HI-05 and a follow-up PR. This module only covers the auth
gap (CR-01).
"""

from __future__ import annotations

import hmac

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from thinktank.admin.dependencies import get_session
from thinktank.secrets import get_secret

# Cookie name used by the admin login endpoint to set a browser session
# after a successful token login.
ADMIN_SESSION_COOKIE = "admin_session"

# Audit-label cookie set alongside the session cookie at login time.
# ADMIN-REVIEW LO-01: routers previously wrote the literal ``"admin"``
# into audit fields (``set_by``, ``reviewed_by``, ``triggered_by``,
# ``overridden_by``). This cookie carries a self-declared display name
# so multiple humans sharing the admin token can still distinguish who
# took an action. It is NOT an authentication credential -- the admin
# token still gates access via ``require_admin`` -- just an audit label.
ADMIN_USER_COOKIE = "admin_user"

# Default principal label when no ``admin_user`` cookie is present
# (token-only auth via ``Authorization: Bearer`` header, or legacy
# sessions opened before this cookie was introduced).
DEFAULT_ADMIN_PRINCIPAL = "admin"

# ``get_secret`` expects the name WITHOUT the ``secret_`` prefix; it
# prefixes internally.
_ADMIN_TOKEN_NAME = "admin_api_token"


async def _extract_presented_token(request: Request) -> str | None:
    """Return the token presented by the caller, if any.

    Checks the ``Authorization: Bearer <token>`` header first, then falls
    back to the ``admin_session`` cookie. Returns ``None`` if neither is
    present.
    """
    header = request.headers.get("Authorization", "")
    if header.startswith("Bearer "):
        return header.removeprefix("Bearer ").strip() or None
    cookie = request.cookies.get(ADMIN_SESSION_COOKIE)
    if cookie:
        return cookie
    return None


_MAX_PRINCIPAL_LEN = 64


def _sanitize_principal(raw: str | None) -> str:
    """Clamp an ``admin_user`` cookie to a safe audit label.

    Falls back to :data:`DEFAULT_ADMIN_PRINCIPAL` on empty/whitespace.
    Strips characters outside ``[A-Za-z0-9 _.@+-]`` so the value is safe
    to round-trip into SQL TEXT audit columns, HTML attributes, and log
    lines without escaping surprises.
    """
    if not raw:
        return DEFAULT_ADMIN_PRINCIPAL
    cleaned = "".join(ch for ch in raw.strip() if ch.isalnum() or ch in " _.@+-")[:_MAX_PRINCIPAL_LEN]
    return cleaned or DEFAULT_ADMIN_PRINCIPAL


async def require_admin(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> str:
    """FastAPI dependency: authorise an admin request.

    Returns the principal label to record in audit columns
    (``set_by``, ``reviewed_by``, ``triggered_by``, ``overridden_by``).
    Derived from the ``admin_user`` cookie if present and sanitized, or
    :data:`DEFAULT_ADMIN_PRINCIPAL` otherwise. ADMIN-REVIEW LO-01.

    Raises:
        HTTPException(500): secret_admin_api_token is not configured.
        HTTPException(401): missing / wrong bearer token / wrong cookie.
    """
    expected = await get_secret(session, _ADMIN_TOKEN_NAME)
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="admin token not configured",
        )

    presented = await _extract_presented_token(request)
    if not presented:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="unauthorized",
        )

    # Constant-time comparison to avoid token length / prefix leaks.
    if not hmac.compare_digest(presented, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="unauthorized",
        )

    return _sanitize_principal(request.cookies.get(ADMIN_USER_COOKIE))
