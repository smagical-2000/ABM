"""Shared logging hygiene for the cron entrypoints.

httpx logs one INFO line per request containing the FULL url. Any secret carried
in a query string therefore lands in Railway's log store, forever, for anyone
with log access — which is exactly how the Apify API key leaked (2026-07-27: the
actor endpoints used `?token=<APIFY_API_KEY>`; run_discovery silenced httpx but
run_social and the engagement legs did not).

The primary fix is to never put a secret in a URL (the clients send
`Authorization: Bearer` now). This is the second layer: one call, so a new cron
leg gets the same hygiene without re-deriving it.
"""

from __future__ import annotations

import logging

# Loggers that echo request URLs or prompt bodies at INFO.
NOISY = ("httpx", "httpcore", "anthropic")


def quiet_http_logs(level: int = logging.WARNING) -> None:
    """Cap the request-logging libraries at WARNING. Call it right after
    logging.basicConfig in every entrypoint that makes HTTP calls."""
    for name in NOISY:
        logging.getLogger(name).setLevel(level)
