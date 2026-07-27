"""Deterministic process exit for the cron entrypoints.

A cron leg opens one psycopg ConnectionPool per repository, and each pool runs
worker threads. On a normal `sys.exit()` CPython finalizes the interpreter,
which tries to join those threads — and can raise
`PythonFinalizationError: cannot join thread at interpreter shutdown` or simply
block. The script has already done all of its work at that point, but the
CONTAINER never exits. That is the most plausible cause of the Jul 24-27
silence, when Railway's cron just stopped ticking: a scheduled service whose
previous invocation is still "running" does not start the next one.

So the entrypoints close their pools, flush the log streams by hand (os._exit
skips the atexit flush), and then hard-exit. There is nothing left to clean up
after a cron leg — every write is already committed.
"""

from __future__ import annotations

import logging
import os
import sys

logger = logging.getLogger(__name__)


def close_pools(objs) -> None:
    """Best-effort `.close()` on anything that has one (repos, pools, clients).
    An object without close() is skipped; a failing close is logged, never
    raised — we are on the way out and the work is already committed."""
    for obj in objs or ():
        fn = getattr(obj, "close", None)
        if not callable(fn):
            continue
        try:
            fn()
        except Exception as e:  # noqa: BLE001 — shutdown must not fail
            logger.debug("close failed for %r: %s", type(obj).__name__, e)


def hard_exit(code: int, *objs) -> None:
    """Close `objs`, flush stdout/stderr, then os._exit(code). Never returns.

    os._exit and not sys.exit: see the module docstring — joining psycopg pool
    threads at interpreter finalization can hang the container forever."""
    close_pools(objs)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except Exception:  # noqa: BLE001
            pass
    logging.shutdown()
    os._exit(int(code))
