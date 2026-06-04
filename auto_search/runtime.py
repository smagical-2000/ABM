"""Runtime environment detection.

One place to answer "are we in production?" so security defaults can fail closed
there (require auth, require Postgres) while staying frictionless on localhost.
Railway always sets RAILWAY_* in a deployed environment; APP_ENV is the explicit
override for any other host.
"""

from __future__ import annotations

import os

_RAILWAY_MARKERS = (
    "RAILWAY_ENVIRONMENT", "RAILWAY_ENVIRONMENT_NAME",
    "RAILWAY_PROJECT_ID", "RAILWAY_SERVICE_ID",
)


def is_production() -> bool:
    if os.getenv("APP_ENV", "").lower() in ("production", "prod"):
        return True
    if os.getenv("APP_ENV", "").lower() in ("dev", "development", "local", "test"):
        return False
    return any(os.getenv(m) for m in _RAILWAY_MARKERS)
