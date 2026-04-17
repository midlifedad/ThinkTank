"""Login endpoint for the admin app.

Provides a minimal token-in / cookie-out flow so browsers can authenticate.
Mounted WITHOUT the ``require_admin`` dependency -- otherwise nobody could
ever log in.

Source: ADMIN-REVIEW CR-01.
"""

from __future__ import annotations

import hmac

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from thinktank.admin.auth import ADMIN_SESSION_COOKIE, ADMIN_USER_COOKIE, _sanitize_principal
from thinktank.admin.dependencies import get_session
from thinktank.secrets import get_secret

router = APIRouter(prefix="/admin", tags=["auth"])


_LOGIN_PAGE_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>ThinkTank Admin Login</title>
</head>
<body>
  <h1>Admin Login</h1>
  <form method="post" action="/admin/login">
    <label>Admin token: <input type="password" name="token" required></label><br>
    <label>Your name (optional, for audit): <input type="text" name="username" maxlength="64"></label>
    <button type="submit">Sign in</button>
  </form>
</body>
</html>
"""


@router.get("/login", response_class=HTMLResponse)
async def login_page() -> HTMLResponse:
    """Render a minimal login form. Publicly reachable by design."""
    return HTMLResponse(_LOGIN_PAGE_HTML)


@router.post("/login")
async def login(
    request: Request,
    response: Response,
    token: str = Form(...),
    username: str = Form(""),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Accept an admin token, set the admin_session cookie on success.

    Optionally records a human-readable ``username`` alongside the
    session cookie as an audit label (ADMIN-REVIEW LO-01). The label is
    NOT used for authorization -- the admin token still gates access --
    it only identifies who performed the action in audit columns
    (``set_by``, ``reviewed_by``, ``triggered_by``, ``overridden_by``).

    Returns 401 on mismatch. Returns 500 if the admin token isn't
    configured in system_config at all (same fail-closed behaviour as
    ``require_admin``).
    """
    expected = await get_secret(session, "admin_api_token")
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="admin token not configured",
        )

    if not hmac.compare_digest(token, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid token",
        )

    # Successful login: set the session cookie. Cookie value is the admin
    # token itself (the server validates it against the DB row on every
    # request via ``require_admin``). Signed-cookie / server-side session
    # storage is a follow-up hardening — captured by ADMIN-REVIEW HI-05.
    response = Response(status_code=status.HTTP_200_OK, content="ok")
    is_https = request.url.scheme == "https"
    response.set_cookie(
        key=ADMIN_SESSION_COOKIE,
        value=expected,
        httponly=True,
        samesite="lax",
        secure=is_https,
        path="/",
    )
    # Audit label cookie (LO-01). Sanitize so a stray header/control char
    # can't end up in SQL TEXT audit columns or log output. Not HttpOnly
    # -- it's a display label, not a credential.
    response.set_cookie(
        key=ADMIN_USER_COOKIE,
        value=_sanitize_principal(username),
        httponly=False,
        samesite="lax",
        secure=is_https,
        path="/",
    )
    return response


@router.post("/logout")
async def logout() -> Response:
    """Clear the admin session and user-label cookies. Publicly reachable (idempotent)."""
    response = Response(status_code=status.HTTP_200_OK, content="ok")
    response.delete_cookie(key=ADMIN_SESSION_COOKIE, path="/")
    response.delete_cookie(key=ADMIN_USER_COOKIE, path="/")
    return response
