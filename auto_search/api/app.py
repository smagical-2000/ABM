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
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from auto_search import discovery_runner
from auto_search.api.auth import install_basic_auth
from auto_search.db import get_repository
from auto_search.db.scoring_repository import (
    STALE_SCORING_SECONDS,
    get_scoring_repository,
)
from auto_search.run_control import RunControl
from auto_search.runtime import is_production
from auto_search.scoring import budget as budget_guard
from auto_search.scoring import imports as csv_imports
from auto_search.scoring import spend_guard
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
            # Should never happen once the app has started; loud so dropped paid
            # work is never silent.
            logger.error("no event loop to schedule background work — DROPPING it")
            coro.close()
            return
        task = asyncio.run_coroutine_threadsafe(coro, loop)
    app.state.scoring_tasks.add(task)
    task.add_done_callback(lambda t: app.state.scoring_tasks.discard(t))


def _claim_scoring(app: FastAPI, account_id: str) -> bool:
    """Claim an account for scoring. Returns False if it is already in flight, so
    promote + a batch + a manual re-score can't run two paid passes at once
    (single-process guard; a DB lock would be needed for multiple workers)."""
    inflight = app.state.scoring_inflight
    if account_id in inflight:
        return False
    inflight.add(account_id)
    return True


def _schedule_scoring(app: FastAPI, account_id: str, *, op_type: str = "score_one") -> None:
    """Background-score one account, guarded so the same account never doubles up.

    Wraps the score in a single-account spend operation so even an ad-hoc score
    or a promote records its cost_events and is held to the per-account cap.
    """
    if not _claim_scoring(app, account_id):
        logger.info("skip scoring %s — already in flight", account_id)
        return

    async def _run() -> None:
        op = spend_guard.Operation(app.state.scoring_repo, op_type,
                                   estimated_usd=budget_guard.EST_SCORE_COST,
                                   accounts_planned=1)
        try:
            await app.state.scoring.run_scoring(account_id, op=op)
        finally:
            op.finish()
            app.state.scoring_inflight.discard(account_id)

    _schedule_coro(app, _run())


async def _run_batch(app: FastAPI, account_ids: list[str], *,
                     op: spend_guard.Operation | None = None) -> None:
    """Score a queued batch with bounded concurrency, then clear the busy flag.

    Layer B (per-operation envelope): once `op` reports overheated, stop
    scheduling NEW accounts — in-flight ones finish — so a batch whose actual
    spend blows past its estimate is halted mid-flight, not after the fact.
    """
    sem = asyncio.Semaphore(_BATCH_CONCURRENCY)
    stop = {"flag": False}

    async def one(account_id: str) -> None:
        if stop["flag"]:
            return
        async with sem:
            if stop["flag"]:
                return
            if not _claim_scoring(app, account_id):
                return                     # already being scored elsewhere
            try:
                await app.state.scoring.run_scoring(account_id, op=op)
            except Exception:  # noqa: BLE001 — one failure must not stop the batch
                logger.exception("batch scoring failed for %s", account_id)
            finally:
                app.state.scoring_inflight.discard(account_id)
            if op is not None and op.overheated() and not stop["flag"]:
                stop["flag"] = True
                logger.warning("batch overheated: spent $%.2f vs est $%.2f — stopping new work",
                               op.actual, op.estimated)

    try:
        await asyncio.gather(*(one(a) for a in account_ids))
    finally:
        if op is not None:
            op.finish()
            app.state.last_overheat = (
                {"actual": round(op.actual, 2), "estimated": round(op.estimated, 2)}
                if op.overheated() else None
            )
        app.state.batch_running = False
    logger.info("batch complete: %d accounts (op=%s, $%.3f)",
                len(account_ids), op.id if op else "—", op.actual if op else 0.0)


def _assert_budget(app: FastAPI, est: float) -> dict:
    """Reject a paid request that would exceed the monthly budget (429)."""
    summary = app.state.scoring_repo.cost_summary()
    try:
        budget_guard.assert_affordable(summary, est)
    except budget_guard.BudgetExceeded as e:
        raise HTTPException(status_code=429, detail=str(e)) from None
    return summary


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


def _import_label(filename: str | None) -> str:
    """A readable label for an import batch: filename plus the date/time, which
    also disambiguates two uploads of the same filename."""
    name = (filename or "").strip() or "import.csv"
    return f"{name} · {datetime.now(UTC).strftime('%b %d, %H:%M')}"


# Upload caps: a CSV import is raw-bodied, so bound it to avoid an OOM body or a
# runaway queue that a later "Score all" could turn into a big spend.
_MAX_UPLOAD_BYTES = 5_000_000   # 5 MB
_MAX_CSV_ROWS = 5_000


def _parse_upload(raw: bytes) -> csv_imports.ImportResult:
    if len(raw) > _MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"CSV too large (limit {_MAX_UPLOAD_BYTES // 1_000_000} MB).")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    try:
        result = csv_imports.parse_csv(text)
    except csv_imports.ImportError_ as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    if result.rows_total > _MAX_CSV_ROWS:
        raise HTTPException(
            status_code=413,
            detail=f"Too many rows ({result.rows_total}); limit is {_MAX_CSV_ROWS}. "
                   "Split the file into smaller imports.")
    return result


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
    app.state.scoring_inflight = set()        # account_ids being scored (dedupe lock)
    app.state.batch_running = False           # one queued batch at a time
    app.state.discovery_running = False       # one on-demand discovery run at a time
    app.state.discovery_control = RunControl()  # live pause/cancel for that run
    app.state.last_discovery = None
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

    # CORS — same-origin in production (the UI is served by this app), permissive
    # in dev so a separately-served UI can call the API. A wildcard origin on a
    # public, spend-bearing API is a hole, so production never defaults to "*".
    cors_env = os.getenv("CORS_ORIGINS")
    if cors_env:
        allow_origins = cors_env.split(",")
    elif is_production():
        allow_origins = []        # same-origin only (browser UI shares the origin)
    else:
        allow_origins = ["*"]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allow_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # HTTP Basic auth — gated by BASIC_AUTH_USER/PASS. In production we FAIL
    # CLOSED: refuse to start without credentials rather than serve a public,
    # spend-bearing API. Localhost (no production markers) stays frictionless.
    # /api/health is exempt for the platform healthcheck; added after CORS so it
    # runs outermost.
    auth_enabled = install_basic_auth(app, exempt_paths=("/api/health",))
    if not auth_enabled and is_production():
        raise RuntimeError(
            "Refusing to start in production without auth: set BASIC_AUTH_USER "
            "and BASIC_AUTH_PASS."
        )

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
        statuses = ("needs_review",) if status == "needs_review" else ("qualified",)
        companies = svc(app).list_panel(
            statuses=statuses, segment=segment, signal_type=signal_type
        )
        try:
            keys = [c.company_key for c in companies]
            costs = app.state.scoring_repo.qualify_costs(keys) if keys else {}
            est = spend_guard.discovery_est_qual_cost()
            return [c.model_copy(update={"qualify_cost_usd": (
                costs.get(c.company_key)
                if costs.get(c.company_key) is not None
                else (est if c.qualified_at else None))})
                    for c in companies]
        except Exception:  # noqa: BLE001 — cost lookup must not break the panel
            logger.exception("panel qualify cost lookup failed")
            return companies

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
        recent = repo.recent_decisions(limit=20) if hasattr(repo, "recent_decisions") else []
        # Attach per-company qualify spend from cost_events (measured tokens).
        try:
            keys = [r["company_key"] for r in recent if r.get("company_key")]
            costs = app.state.scoring_repo.qualify_costs(keys) if keys else {}
            est = spend_guard.discovery_est_qual_cost()
            for r in recent:
                key = r.get("company_key")
                c = costs.get(key) if key else None
                # Older runs logged one bulk event without company_key — show the
                # estimate so historical rows aren't blank.
                r["cost_usd"] = c if c is not None else (
                    est if r.get("status") in ("qualified", "needs_review", "disqualified")
                    else None)
        except Exception:  # noqa: BLE001 — cost lookup is best-effort for the log
            logger.exception("qualify cost lookup failed")
        ctrl = app.state.discovery_control
        return {"active": active, "recent": recent,
                "running": bool(getattr(app.state, "discovery_running", False)),
                "paused": ctrl.paused,
                "cancelling": ctrl.cancelled,
                "last_run": getattr(app.state, "last_discovery", None)}

    @app.post("/api/discovery/run")
    async def discovery_run(request: Request):
        """Manually pull the last 24h of signals into the panel, on demand.

        Runs the browserless sources (leadership, acquisitions, funding, jobs) in
        the background, deduped; the existing activity poll shows it processing
        and qualified companies stream into the panel. Layoffs (WARN) needs a
        browser the web image omits, so it stays with the cron worker. One run at
        a time so a double click can't double-spend.

        Optional JSON body for cost-controlled test runs:
          {"sources": ["jobs"], "limit": 2}
        `sources` restricts which browserless sources run; `limit` caps unique
        companies qualified PER SOURCE (the spend knob). Omit both for a full
        24h pull.
        """
        if getattr(app.state, "discovery_running", False):
            return {"started": False, "busy": True}
        # Cost control for panel 1: refuse a manual run once this month's
        # discovery (qualify) spend has hit its budget, so the cheap-but-not-free
        # qualifier can't be clicked into a runaway. Tune DISCOVERY_MONTHLY_BUDGET.
        try:
            rollup = app.state.scoring_repo.spend_rollup()
            disc_budget = spend_guard.discovery_monthly_budget()
            disc_spent = float(rollup.get("month_discovery_cost") or 0)
            if disc_budget and disc_spent >= disc_budget:
                return {"started": False, "budget_blocked": True,
                        "month_discovery_cost": round(disc_spent, 2),
                        "discovery_budget": disc_budget}
        except Exception:  # noqa: BLE001 — never let the meter block a run by erroring
            logger.exception("discovery budget check failed; allowing run")

        body = await _json_body(request)
        raw_sources = body.get("sources")
        sources = None
        if isinstance(raw_sources, list) and raw_sources:
            sources = [s for s in raw_sources if s in discovery_runner.BROWSERLESS_SOURCES]
            if not sources:
                raise HTTPException(status_code=400, detail=(
                    "sources must be a subset of "
                    f"{list(discovery_runner.BROWSERLESS_SOURCES)}"))
        limit = body.get("limit")
        if not isinstance(limit, int) or limit <= 0:
            limit = None

        ctrl = app.state.discovery_control
        ctrl.reset()
        app.state.discovery_running = True

        # Worst-case estimate for the spend guard envelope (prevents false "overheated").
        n_sources = len(sources or discovery_runner.BROWSERLESS_SOURCES)
        est_companies = (limit or 0) * n_sources if limit else 0
        est_usd = round(est_companies * spend_guard.discovery_est_qual_cost(), 4) if est_companies else 0.0

        async def _run() -> None:
            op = spend_guard.Operation(
                app.state.scoring_repo, "discovery_manual",
                estimated_usd=est_usd, accounts_planned=est_companies or 0,
                metadata={"sources": sources or list(discovery_runner.BROWSERLESS_SOURCES),
                          "limit": limit},
            )

            def on_company(cand) -> None:
                spend_guard.record_company_qualify(op, cand)

            try:
                summary = await discovery_runner.run_once(
                    app.state.repo, days=1, sources=sources, limit=limit,
                    on_company=on_company, gate=ctrl.gate)
                summary["cost_usd"] = round(op.actual, 4)
                app.state.last_discovery = {**summary, "at": datetime.now(UTC).isoformat()}
            except Exception:  # noqa: BLE001 — never crash the loop
                logger.exception("on-demand discovery run failed")
            finally:
                op.finish()
                app.state.discovery_running = False

        _schedule_coro(app, _run())
        return {"started": True,
                "sources": sources or list(discovery_runner.BROWSERLESS_SOURCES),
                "limit": limit}

    @app.post("/api/discovery/pause")
    def discovery_pause():
        """Pause the in-flight discovery run at the next company boundary — no
        new Claude qualification starts, so spend freezes until resumed."""
        if not getattr(app.state, "discovery_running", False):
            return {"running": False, "paused": False}
        app.state.discovery_control.pause()
        return {"running": True, "paused": True}

    @app.post("/api/discovery/resume")
    def discovery_resume():
        """Resume a paused run from exactly where it stopped."""
        app.state.discovery_control.resume()
        return {"running": bool(getattr(app.state, "discovery_running", False)),
                "paused": False}

    @app.post("/api/discovery/cancel")
    def discovery_cancel():
        """Cancel the in-flight run. It stops cleanly at the next company
        boundary (any in-flight call finishes). Re-running later 'smart resumes':
        already-qualified companies are skipped by the dedup ledger, so it picks
        up where it left off without paying twice."""
        app.state.discovery_control.cancel()
        return {"cancelling": True,
                "running": bool(getattr(app.state, "discovery_running", False))}

    @app.post("/api/discovery/delete")
    async def discovery_delete(request: Request):
        """Delete discovered companies (and their signals) from the panel store.

        Body: {"keys": ["acmehealth", ...]} to delete specific companies, or
        {"all": true} to wipe the whole discovery store — useful for a clean
        slate between cost-control test runs. Deletion removes the dedup-ledger
        row too, so a deleted company CAN be re-discovered (and re-qualified) on
        the next run.
        """
        if not hasattr(app.state.repo, "delete"):
            raise HTTPException(status_code=501, detail="delete not supported by repo")
        body = await _json_body(request)
        if body.get("all") is True:
            n = app.state.repo.delete(None)
            return {"deleted": n, "all": True}
        keys = body.get("keys")
        if not isinstance(keys, list) or not keys:
            raise HTTPException(status_code=400,
                                detail="provide keys: [...] or all: true")
        n = app.state.repo.delete([str(k) for k in keys])
        return {"deleted": n}

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
        # Budget-aware: auto-score only if there's headroom, else park it as
        # 'queued' (the promote still succeeds; it just doesn't spend over budget).
        summary = app.state.scoring_repo.cost_summary()
        affordable = budget_guard.remaining(summary) >= budget_guard.EST_SCORE_COST
        row = app.state.scoring.enqueue_discovery(
            company.model_dump(), state="scoring" if affordable else "queued")
        if affordable:
            _schedule_scoring(app, row["account_id"], op_type="promote")
        return {"account_id": row["account_id"], "state": row["state"],
                "budget_blocked": not affordable}

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
        _assert_budget(app, budget_guard.EST_SCORE_COST)
        if account_id in app.state.scoring_inflight:
            return app.state.scoring.get(account_id)     # already scoring; no double-spend
        app.state.scoring_repo.set_state(account_id, "scoring")
        _schedule_scoring(app, account_id)
        return app.state.scoring.get(account_id)

    @app.post("/api/account/{account_id}/dossier")
    def generate_dossier(account_id: str):
        """Generate the deep-research landing-page dossier for a scored account.

        On demand only (it costs ~$0.50-1.00), one at a time per account. The UI
        polls GET /api/account/{id} until dossier_state flips to 'ready'."""
        account = app.state.scoring.get(account_id)
        if account is None:
            raise HTTPException(status_code=404, detail="account not found")
        if account.get("state") != "scored":
            raise HTTPException(status_code=409, detail="account must be scored first")
        if account.get("dossier_state") == "generating":
            return account                            # already in flight
        _assert_budget(app, budget_guard.EST_DOSSIER_COST)
        app.state.scoring_repo.set_dossier_state(account_id, "generating")

        async def _run() -> None:
            op = spend_guard.Operation(app.state.scoring_repo, "dossier",
                                       estimated_usd=budget_guard.EST_DOSSIER_COST,
                                       accounts_planned=1)
            try:
                await app.state.scoring.generate_dossier(account_id, op=op)
            finally:
                op.finish()

        _schedule_coro(app, _run())
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
        large file never spends money by itself. Known accounts are skipped.

        Each batch is tagged with a label (the uploaded filename + time) so the
        user can later filter to, and export, exactly what they uploaded."""
        result = _parse_upload(await request.body())
        fresh = [a for a in result.accounts if not app.state.scoring.exists(a.account_id)]
        label = _import_label(request.headers.get("x-import-filename"))
        app.state.scoring.enqueue_csv(fresh, state="queued", import_label=label)
        return {
            "schema_label": result.schema_label,
            "segment": result.segment,
            "imported": len(fresh),
            "queued": len(fresh),
            "skipped_known": len(result.accounts) - len(fresh),
            "import_label": label,
            "accounts": [app.state.scoring.get(a.account_id) for a in fresh],
        }

    @app.get("/api/scoring/imports")
    def scoring_imports():
        """The distinct CSV import batches (label + count), newest first — feeds
        the Import filter so a user can isolate and export their own upload."""
        return {"imports": app.state.scoring_repo.import_labels()}

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
        # Hard budget cap, server-side: never start more than fits the month's
        # budget, no matter what limit (or none) the caller asked for. This is the
        # rule the UI's "score within budget" only suggests.
        requested = len(targets)
        summary = app.state.scoring_repo.cost_summary()
        est = summary.get("avg_cost") or budget_guard.EST_SCORE_COST
        affordable = budget_guard.affordable_count(summary, est)
        if affordable <= 0:
            return {"started": 0, "busy": False, "budget_blocked": True, "budget": summary}
        targets = targets[:affordable]
        # Layer B pre-flight: estimate the batch; a large one needs an explicit
        # confirm_large_spend (still inside the monthly budget cap above).
        est_each = summary.get("csv_avg_cost") or summary.get("avg_cost") or budget_guard.EST_SCORE_COST
        estimate = spend_guard.estimate_batch(len(targets), est_each)
        if spend_guard.needs_confirmation(estimate) and body.get("confirm_large_spend") is not True:
            raise HTTPException(status_code=400, detail={
                "error": "confirm_large_spend_required",
                "estimated_usd": estimate, "accounts": len(targets),
                "threshold_usd": spend_guard.max_op_estimate(),
            })
        op = spend_guard.Operation(app.state.scoring_repo, "score_batch",
                                   estimated_usd=estimate, accounts_planned=len(targets))
        app.state.batch_running = True
        _schedule_coro(app, _run_batch(app, targets, op=op))
        return {"started": len(targets), "busy": True,
                "budget_capped": len(targets) < requested,
                "estimated_usd": estimate, "operation_id": op.id, "budget": summary}

    @app.post("/api/scoring/reset")
    async def scoring_reset(request: Request):
        """Clear every score back to 'queued' (non-destructive) so the table is
        clean and accounts can be re-scored on demand to re-measure cost.

        Requires an explicit {"confirm": true} body so a stray call can't wipe
        every score, and is refused while a batch is mid-run."""
        body = await _json_body(request)
        if body.get("confirm") is not True:
            raise HTTPException(status_code=400, detail="reset requires {\"confirm\": true}")
        if getattr(app.state, "batch_running", False):
            return {"reset": 0, "busy": True}
        n = app.state.scoring_repo.reset_to_queued()
        logger.info("reset %d scored account(s) -> queued", n)
        return {"reset": n, "busy": False}

    @app.get("/api/scoring/stats")
    def scoring_stats():
        """Spend summary for the live cost meter: month-to-date vs budget, the
        scoring/discovery/daily rollup, and the recent spend operations."""
        summary = app.state.scoring_repo.cost_summary()
        summary["batch_running"] = bool(getattr(app.state, "batch_running", False))
        try:
            summary.update(app.state.scoring_repo.spend_rollup())
            summary["last_operations"] = app.state.scoring_repo.recent_operations(8)
        except Exception:  # noqa: BLE001 — rollup is best-effort, never break the meter
            logger.exception("spend rollup failed")
        summary["last_overheat"] = getattr(app.state, "last_overheat", None)
        return summary

    @app.get("/api/health")
    def health():
        return {"ok": True}

    # ── static UI (mounted last so /api/* wins) ────────────────────────
    if _WEB_DIR.is_dir():
        app.mount("/", StaticFiles(directory=str(_WEB_DIR), html=True), name="ui")

    return app


app = create_app()
