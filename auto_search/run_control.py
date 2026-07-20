"""Live pause / resume / cancel control for a long-running discovery run.

Why this exists
---------------
A manual discovery run qualifies one company at a time, and each company is one
(paid) Claude call. To give the operator real cost control we need to be able to
*stop spending* mid-run without corrupting state or wasting an in-flight call.

The mechanism is deliberately tiny and cooperative: the pipeline awaits
`RunControl.gate()` at each company boundary (see `pipeline.RunGate`). The gate

  • blocks while `paused` is set         → no new qualification starts, spend freezes
  • returns False once `cancelled` is set → the pipeline stops cleanly after the
                                            current company, never mid-call

Because it only acts at company boundaries, pause/cancel can never interrupt a
call already in flight (that call finishes and is saved — never paid-for-nothing).

This object is intentionally framework-free (no FastAPI, no DB) so it is trivial
to unit test and to reason about: it is just two booleans and an async gate. The
API layer owns one instance on `app.state`, flips the booleans from HTTP
handlers, and passes `gate` down into the runner/pipeline.
"""

from __future__ import annotations

import asyncio

# How often the gate re-checks the flags while paused. Small enough that a
# resume/cancel feels instant to the operator, large enough to not busy-spin.
_POLL_SECONDS = 0.4


class RunControl:
    """Shared pause/cancel state for the single in-flight discovery run.

    One instance lives for the lifetime of the app and is reused across runs;
    each new run must call :meth:`reset` first so a prior cancel/pause can never
    leak into the next one.

    SINGLE-OWNER ASSUMPTION: the discovery run and the social scan deliberately
    share this one instance (so the UI has one banner + one pause/cancel). That is
    only safe because they are mutually exclusive — each entry point refuses to
    start if either `discovery_running` or `social_running` is set. If you ever
    let both run at once, give each its own RunControl: a shared one would
    cross-wire (pausing one pauses the other; whichever finishes first resets the
    flags the other still needs).
    """

    def __init__(self) -> None:
        self.paused = False
        self.cancelled = False

    def reset(self) -> None:
        """Clear state for a fresh run. Call this before starting one."""
        self.paused = False
        self.cancelled = False

    def pause(self) -> None:
        self.paused = True

    def resume(self) -> None:
        self.paused = False

    def cancel(self) -> None:
        """Request a clean stop. Also clears `paused` so a paused run can
        observe the cancel and unblock (otherwise it would sleep forever)."""
        self.cancelled = True
        self.paused = False

    async def gate(self) -> bool:
        """Pipeline checkpoint: block while paused, return False if cancelled.

        Returns True to let the caller proceed with the next company.
        """
        while self.paused and not self.cancelled:
            await asyncio.sleep(_POLL_SECONDS)
        return not self.cancelled
