"""HTTP Basic auth gate for the Discovery app.

Why Basic auth: the app serves the UI and the API from one origin, and the UI
calls /api/* from the browser. Basic auth covers BOTH with zero UI code — the
browser shows a login prompt and replays the credentials on every request
(including fetch). One shared user/password is enough for an internal tool.

Config (env):
    BASIC_AUTH_USER, BASIC_AUTH_PASS

If EITHER is unset, auth is DISABLED — so localhost dev stays frictionless and
only a deployed instance (where you set the vars) is gated. /api/health is
always exempt so platform healthchecks pass unauthenticated.
"""

from __future__ import annotations

import base64
import logging
import secrets

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger(__name__)

_REALM = 'Basic realm="Magical Discovery", charset="UTF-8"'


class BasicAuthMiddleware(BaseHTTPMiddleware):
    """Require HTTP Basic credentials on every request except exempt paths."""

    def __init__(self, app, *, user: str, password: str,
                 exempt_paths: tuple[str, ...] = ()) -> None:
        super().__init__(app)
        self._user = user
        self._password = password
        self._exempt = set(exempt_paths)

    async def dispatch(self, request: Request, call_next):
        if request.url.path in self._exempt or self._authorized(
            request.headers.get("Authorization")
        ):
            return await call_next(request)
        return Response(
            "Authentication required.",
            status_code=401,
            headers={"WWW-Authenticate": _REALM},
        )

    def _authorized(self, header: str | None) -> bool:
        if not header or not header.startswith("Basic "):
            return False
        try:
            user, _, pw = base64.b64decode(header[6:]).decode("utf-8").partition(":")
        except (ValueError, UnicodeDecodeError):
            return False
        # constant-time compares to avoid leaking length/timing
        return (
            secrets.compare_digest(user, self._user)
            and secrets.compare_digest(pw, self._password)
        )


def install_basic_auth(app, *, exempt_paths: tuple[str, ...] = ()) -> bool:
    """Add Basic-auth middleware iff BASIC_AUTH_USER + BASIC_AUTH_PASS are set.

    Returns True if auth was enabled. Logs a clear warning when it isn't, so a
    public deploy without credentials is obvious in the logs.
    """
    import os

    user = os.getenv("BASIC_AUTH_USER")
    password = os.getenv("BASIC_AUTH_PASS")
    if not user or not password:
        logger.warning(
            "BASIC_AUTH_USER/PASS not set — API is UNAUTHENTICATED. "
            "Set both before exposing a public URL."
        )
        return False
    app.add_middleware(
        BasicAuthMiddleware, user=user, password=password,
        exempt_paths=exempt_paths,
    )
    logger.info("HTTP Basic auth enabled")
    return True
