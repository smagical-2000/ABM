"""FastAPI app for the Discovery Panel.

Endpoints (all under /api), backed by ReviewService → repository:

    GET  /api/stats                       → DiscoveryStats
    GET  /api/panel?segment=&signal_type= → list[PanelCompany]  (qualified, pending)
    GET  /api/company/{key}               → PanelCompany
    POST /api/company/{key}/promote       → { account_id }
    POST /api/company/{key}/reject        → { ok }   body: { reason }
    POST /api/company/{key}/defer         → { ok }

The static Discovery Panel UI is served at / from web/discovery/.

Handlers are sync `def` on purpose: the repository is sync (one interface for
JSON + Postgres), and FastAPI runs sync handlers in a threadpool, so a brief
DB call never blocks the event loop. The repository/service is built once at
startup and the Postgres pool is closed on shutdown.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from auto_search.api.auth import install_basic_auth
from auto_search.db import get_repository
from auto_search.db.scoring_repository import (
    STALE_SCORING_SECONDS,
    get_scoring_repository,
)
from auto_search.scoring import imports as csv_imports
from auto_search.scoring.frameworks import all_frameworks_public
from auto_search.scoring.service import ScoringService
from auto_search.services import DiscoveryStats, PanelCompany, ReviewService

load_dotenv(override=True)
logger = logging.getLogger(__name__)

_WEB_DIR = Path(__file__).resolve().parent.parent.parent / "web" / "discovery"


class RejectBody(BaseModel):
    reason: str


# Max simultaneous Claude scoring calls when running a queued batch. Bounded so
# a "Score all" over hundreds of accounts paces the spend + respects rate limits
# instead of firing every call at once.
_BATCH_CONCURRENCY = 4


def _schedule_coro(app: FastAPI, coro) -> None:
    """Run a coroutine in the background, callable from sync or async handlers.

    Sync handlers run in a threadpool with no running loop, so we hand the
    coroutine to the main loop captured at startup; async handlers schedule it
    on their own loop. Either way the HTTP response returns immediately.
    """
    try:
        loop = asyncio.get_running_loop()
        task = loop.create_task(coro)
    except RuntimeError:
        loop = getattr(app.state, "loop", None)
        if loop is None:
            coro.close()
            return
        task = asyncio.run_coroutine_threadsafe(coro, loop)
    app.state.scoring_tasks.add(task)
    task.add_done_callback(lambda t: app.state.scoring_tasks.discard(t))


def _schedule_scoring(app: FastAPI, account_id: str) -> None:
    """Background-score one account; the UI shows the live 'Scoring…' state."""
    _schedule_coro(app, app.state.scoring.run_scoring(account_id))


async def _run_batch(app: FastAPI, account_ids: list[str]) -> None:
    """Score a queued batch with bounded concurrency, then clear the busy flag."""
    sem = asyncio.Semaphore(_BATCH_CONCURRENCY)

    async def one(account_id: str) -> None:
        async with sem:
            try:
                await app.state.scoring.run_scoring(account_id)
            except Exception:  # noqa: BLE001 — one failure must not stop the batch
                logger.exception("batch scoring failed for %s", account_id)

    try:
        await asyncio.gather(*(one(a) for a in account_ids))
    finally:
        app.state.batch_running = False
    logger.info("batch complete: %d accounts", len(account_ids))


async def _json_body(request: Request) -> dict:
    """Parse an optional JSON request body, tolerating an empty one."""
    raw = await request.body()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def _parse_upload(raw: bytes) -> csv_imports.ImportResult:
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    try:
        return csv_imports.parse_csv(text)
    except csv_imports.ImportError_ as e:
        raise HTTPException(status_code=400, detail=str(e)) from None


def _preview_payload(app: FastAPI, result: csv_imports.ImportResult) -> dict:
    """Schema + mapping + first rows + dedupe, for the import wizard's review."""
    rows = []
    for a in result.accounts[:12]:
        known = app.state.scoring.exists(a.account_id)
        fact = next(iter(a.firmographics.values()), None)
        rows.append({
            "name": a.name,
            "fact": fact,
            "emr": (a.firmographics.get("EHR Inpatient")
                    or a.firmographics.get("Ambulatory EMR")),
            "dedupe": "known" if known else "new",
        })
    new = sum(1 for a in result.accounts if not app.state.scoring.exists(a.account_id))
    return {
        "schema_label": result.schema_label,
        "segment": result.segment,
        "rows_total": result.rows_total,
        "mapping": [{"col": m.col, "fact": m.fact} for m in result.mapping],
        "unmatched_columns": result.unmatched_columns,
        "preview": rows,
        "new_count": new,
        "known_count": len(result.accounts) - new,
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Build the service once; the repo (Postgres pool or JSON file) lives for
    # the app's lifetime. Stored on app.state so handlers reuse it.
    repo = get_repository()
    scoring_repo = get_scoring_repository()
    # Fresh deploy self-initialises its tables (idempotent).
    for r in (repo, scoring_repo):
        ensure = getattr(r, "ensure_schema", None)
        if callable(ensure):
            ensure()
    app.state.service = ReviewService(repo)
    app.state.scoring = ScoringService(scoring_repo)
    app.state.repo = repo
    app.state.scoring_repo = scoring_repo
    app.state.scoring_tasks = set()           # keep background score tasks alive
    app.state.batch_running = False           # one queued batch at a time
    app.state.loop = asyncio.get_running_loop()
    # No scoring task can be alive at boot, so anything still marked 'scoring'
    # was orphaned by the previous shutdown — return it to the queue so it does
    # not tick "scoring" forever, and is re-scoreable on demand.
    orphaned = scoring_repo.recover_orphaned_scoring()
    if orphaned:
        logger.warning("recovered %d orphaned 'scoring' account(s) -> queued", orphaned)
    logger.info("discovery + scoring API ready (repo=%s)", type(repo).__name__)
    try:
        yield
    finally:
        for r in (repo, scoring_repo):
            close = getattr(r, "close", None)
            if callable(close):
                close()


def create_app() -> FastAPI:
    app = FastAPI(title="Magical Discovery API", lifespan=lifespan)

    # CORS — permissive in dev so a separately-served UI can call the API.
    # Tighten allow_origins to the real UI origin in production.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=os.getenv("CORS_ORIGINS", "*").split(","),
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # HTTP Basic auth — enabled iff BASIC_AUTH_USER/PASS are set (so a deployed
    # instance is gated but localhost isn't). /api/health stays open for the
    # platform healthcheck. Added after CORS so it runs outermost (first).
    install_basic_auth(app, exempt_paths=("/api/health",))

    @app.middleware("http")
    async def ui_no_cache(request, call_next):
        """Force revalidation of the UI assets.

        The Discovery UI loads app.jsx / panel.jsx / … and transpiles them in
        the browser with no cache-busting query string. Without this, a browser
        serves the previously-cached JSX after a deploy, so changes appear to
        "not reflect" until a hard refresh. no-cache makes the browser
        revalidate every load, so a deploy always shows up.
        """
        response = await call_next(request)
        if not request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-cache, must-revalidate"
        return response

    def svc(app: FastAPI) -> ReviewService:
        return app.state.service

    # ── reads ──────────────────────────────────────────────────────────

    @app.get("/api/stats", response_model=DiscoveryStats)
    def get_stats():
        return svc(app).stats()

    @app.get("/api/panel", response_model=list[PanelCompany])
    def get_panel(
        status: str = "qualified",
        segment: str | None = None,
        signal_type: str | None = None,
    ):
        # `status` selects the tab:
        #   qualified (default) / needs_review → pending queue for that verdict
        #   deferred                          → snoozed companies (restorable)
        if status == "deferred":
            return svc(app).list_panel(
                statuses=("qualified", "needs_review"),
                review_status="deferred",
                segment=segment, signal_type=signal_type,
            )
        statuses = ("needs_review",) if status == "needs_review" else ("qualified",)
        return svc(app).list_panel(
            statuses=statuses, segment=segment, signal_type=signal_type
        )

    @app.get("/api/activity")
    def get_activity():
        """Powers the live marker + per-account feed.

        Returns:
          active: in-progress runs (drives the "Discovering…" banner)
          recent: most-recently decided companies, newest first (drives the
                  fading corner feed: "✅ Acme — qualified", "❌ Foo — ...")
        Defensive: a repo without run tracking just reports idle/empty.
        """
        repo = app.state.repo
        active = repo.active_runs() if hasattr(repo, "active_runs") else []
        recent = repo.recent_decisions() if hasattr(repo, "recent_decisions") else []
        return {"active": active, "recent": recent}

    @app.get("/api/company/{key}", response_model=PanelCompany)
    def get_company(key: str):
        company = svc(app).get_company(key)
        if company is None:
            raise HTTPException(status_code=404, detail="company not found")
        return company

    # ── workflow ───────────────────────────────────────────────────────

    @app.post("/api/company/{key}/promote")
    def promote(key: str):
        """Promote a qualified company into scoring.

        Marks it promoted in Discovery (so it leaves the panel), creates the
        scoring account carrying its signals, and kicks off scoring in the
        background. The UI shows it arrive in Scored with a live 'Scoring…'
        state.
        """
        company = svc(app).get_company(key)
        if company is None:
            raise HTTPException(status_code=404, detail="company not found")
        try:
            svc(app).promote(key)
        except KeyError:
            raise HTTPException(status_code=404, detail="company not found") from None
        row = app.state.scoring.enqueue_discovery(company.model_dump(), state="scoring")
        _schedule_scoring(app, row["account_id"])
        return {"account_id": row["account_id"], "state": row["state"]}

    @app.post("/api/company/{key}/reject")
    def reject(key: str, body: RejectBody):
        try:
            svc(app).reject(key, reason=body.reason)
        except KeyError:
            raise HTTPException(status_code=404, detail="company not found") from None
        return {"ok": True}

    @app.post("/api/company/{key}/defer")
    def defer(key: str):
        try:
            svc(app).defer(key)
        except KeyError:
            raise HTTPException(status_code=404, detail="company not found") from None
        return {"ok": True}

    @app.post("/api/company/{key}/restore")
    def restore(key: str):
        """Move a deferred company back to the pending queue."""
        try:
            svc(app).restore(key)
        except KeyError:
            raise HTTPException(status_code=404, detail="company not found") from None
        return {"ok": True}

    # ── scoring ────────────────────────────────────────────────────────

    @app.get("/api/scoring/frameworks")
    def scoring_frameworks():
        """Rubric definitions (dimensions, bands, pillar rollup) — the single
        source the UI reads so its score bars and tiers can't drift."""
        return all_frameworks_public()

    @app.get("/api/scored")
    def list_scored():
        """Every account in the scoring phase (queued / scoring / scored / error).
        The dashboard filters client-side."""
        return app.state.scoring.list_scored()

    @app.get("/api/scoring/activity")
    def scoring_activity():
        """Actively-scoring accounts — drives the live shimmer. Also self-heals:
        any score stalled past the threshold (a dead task) is swept back to the
        queue here, so the UI never shows a forever-scoring row."""
        reaped = app.state.scoring_repo.recover_orphaned_scoring(STALE_SCORING_SECONDS)
        if reaped:
            logger.warning("swept %d stalled 'scoring' account(s) -> queued", reaped)
        return {"active": app.state.scoring.active()}

    @app.get("/api/account/{account_id}")
    def get_account(account_id: str):
        account = app.state.scoring.get(account_id)
        if account is None:
            raise HTTPException(status_code=404, detail="account not found")
        return account

    @app.post("/api/account/{account_id}/score")
    def score_account(account_id: str):
        """Score or re-score an account now. Flips it to 'scoring' and kicks the
        background pass; the UI polls activity until it resolves."""
        if not app.state.scoring.exists(account_id):
            raise HTTPException(status_code=404, detail="account not found")
        app.state.scoring_repo.set_state(account_id, "scoring")
        _schedule_scoring(app, account_id)
        return app.state.scoring.get(account_id)

    @app.post("/api/scoring/import/preview")
    async def import_preview(request: Request):
        """Parse a CSV (raw request body) and report the schema + column mapping
        + dedupe, without persisting — the wizard's review step."""
        result = _parse_upload(await request.body())
        return _preview_payload(app, result)

    @app.post("/api/scoring/import")
    async def import_commit(request: Request):
        """Parse the CSV body and enqueue the new accounts as 'queued' — parked,
        NOT scored. Scoring is on demand (per-account or a batch) so importing a
        large file never spends money by itself. Known accounts are skipped."""
        result = _parse_upload(await request.body())
        fresh = [a for a in result.accounts if not app.state.scoring.exists(a.account_id)]
        app.state.scoring.enqueue_csv(fresh, state="queued")
        return {
            "schema_label": result.schema_label,
            "segment": result.segment,
            "imported": len(fresh),
            "queued": len(fresh),
            "skipped_known": len(result.accounts) - len(fresh),
            "accounts": [app.state.scoring.get(a.account_id) for a in fresh],
        }

    @app.post("/api/scoring/score-queued")
    async def score_queued(request: Request):
        """Score parked (queued) accounts in a bounded background batch.

        The spend guardrail: imports land queued for free, and the user scores
        them on demand here. Optional body {"limit": N} or {"account_ids": [...]}
        to score a slice; default scores every queued account. One batch runs at
        a time so a second click can't double-spend.
        """
        if getattr(app.state, "batch_running", False):
            return {"started": 0, "busy": True}
        body = await _json_body(request)
        queued_ids = [q["account_id"] for q in app.state.scoring_repo.queued()]
        ids = body.get("account_ids")
        if isinstance(ids, list) and ids:
            wanted = set(ids)
            targets = [a for a in queued_ids if a in wanted]
        else:
            targets = queued_ids
            limit = body.get("limit")
            if isinstance(limit, int) and limit > 0:
                targets = targets[:limit]
        if not targets:
            return {"started": 0, "busy": False}
        app.state.batch_running = True
        _schedule_coro(app, _run_batch(app, targets))
        return {"started": len(targets), "busy": True}

    @app.post("/api/scoring/reset")
    def scoring_reset():
        """Clear every score back to 'queued' (non-destructive) so the table is
        clean and accounts can be re-scored on demand to re-measure cost. Refused
        while a batch is mid-run so it can't fight in-flight saves."""
        if getattr(app.state, "batch_running", False):
            return {"reset": 0, "busy": True}
        n = app.state.scoring_repo.reset_to_queued()
        logger.info("reset %d scored account(s) -> queued", n)
        return {"reset": n, "busy": False}

    @app.get("/api/scoring/stats")
    def scoring_stats():
        """Spend summary for the live cost meter: month-to-date vs budget, total,
        average per account, and how many accounts are parked in 'queued'."""
        summary = app.state.scoring_repo.cost_summary()
        summary["batch_running"] = bool(getattr(app.state, "batch_running", False))
        return summary

    @app.get("/api/health")
    def health():
        return {"ok": True}

    # ── static UI (mounted last so /api/* wins) ────────────────────────
    if _WEB_DIR.is_dir():
        app.mount("/", StaticFiles(directory=str(_WEB_DIR), html=True), name="ui")

    return app


app = create_app()
