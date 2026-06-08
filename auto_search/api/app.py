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
import re
import secrets
from collections import deque
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from auto_search import discovery_runner
from auto_search.abm import (
    AbmIndex,
    TargetAccount,
    match_one,
    parse_workbook,
    states_from_locations,
)
from auto_search.api.auth import install_basic_auth
from auto_search.db import get_repository
from auto_search.db.scoring_repository import (
    STALE_SCORING_SECONDS,
    get_scoring_repository,
)
from auto_search.normalize import normalize_linkedin_url
from auto_search.run_control import RunControl
from auto_search.runtime import is_production
from auto_search.scoring import budget as budget_guard
from auto_search.scoring import imports as csv_imports
from auto_search.scoring import spend_guard
from auto_search.scoring.frameworks import all_frameworks_public
from auto_search.scoring.service import ScoringService
from auto_search.services import DiscoveryStats, PanelCompany, ReviewService
from auto_search.social import (
    SocialTarget,
    engager_from_trigify,
    ingest_engager,
    poll_events,
    poll_targets,
)

load_dotenv(override=True)
logger = logging.getLogger(__name__)

# Manual-run date windows: likes/comments use a posted-since date (days), event
# search uses the actor's enum. The cron + the main Run button use "24h".
_WINDOW_DAYS = {"24h": 1, "week": 7, "month": 30}
_WINDOW_FILTER = {"24h": "past-24h", "week": "past-week", "month": "past-month"}

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


def _load_abm_index(repo) -> AbmIndex:
    """Build the ABM match index from the persisted target list (empty if none)."""
    rows = repo.abm_targets() if hasattr(repo, "abm_targets") else []
    return AbmIndex([TargetAccount(**r) for r in rows])


# Magical's own LinkedIn — always monitored (its engagers are the hottest signal).
_MAGICAL_TARGET = {
    "linkedin_url": "https://www.linkedin.com/company/getmagical",
    "label": "Magical", "kind": "own", "active": True,
}


def _seed_social_targets(repo) -> None:
    """Ensure Magical's own account is in the monitored list (idempotent)."""
    if not hasattr(repo, "upsert_social_target"):
        return
    try:
        existing = {normalize_linkedin_url(t.get("linkedin_url"))
                    for t in repo.social_targets()}
        if normalize_linkedin_url(_MAGICAL_TARGET["linkedin_url"]) not in existing:
            repo.upsert_social_target(dict(_MAGICAL_TARGET))
    except Exception:  # noqa: BLE001 — seeding must never block startup
        logger.exception("social target seed failed")


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
    app.state.abm_index = _load_abm_index(repo)   # ABM target list -> match index
    _seed_social_targets(repo)                    # ensure Magical is always monitored
    # Ring buffer of the last social-webhook payloads + outcomes, for debugging
    # the Trigify field mapping (read via GET /api/social/debug). In-memory only.
    app.state.social_debug = deque(maxlen=50)
    app.state.social_running = False              # one social poll at a time
    app.state.scoring_tasks = set()           # keep background score tasks alive
    app.state.scoring_inflight = set()        # account_ids being scored (dedupe lock)
    app.state.batch_running = False           # one queued batch at a time
    app.state.discovery_running = False       # one on-demand discovery run at a time
    app.state.discovery_control = RunControl()  # live pause/cancel — shared by social
    app.state.last_discovery = None
    app.state.run_phase = None                # label for the live banner (which run)
    app.state.loop = asyncio.get_running_loop()
    # No scoring task can be alive at boot, so anything still marked 'scoring'
    # was orphaned by the previous shutdown — return it to the queue so it does
    # not tick "scoring" forever, and is re-scoreable on demand.
    orphaned = scoring_repo.recover_orphaned_scoring()
    if orphaned:
        logger.warning("recovered %d orphaned 'scoring' account(s) -> queued", orphaned)
    # A discovery run lives in-memory; rows left 'running' by a prior crash/restart
    # have no process behind them. Clear them so the panel can't show a phantom
    # in-progress run (stale progress, dead pause/cancel).
    cleanup = getattr(repo, "cleanup_stale_runs", None)
    if callable(cleanup):
        stale = cleanup()
        if stale:
            logger.warning("cleared %d orphaned discovery run(s) from a prior process", stale)
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
    # /api/health is exempt for the platform healthcheck; /api/social/trigify is
    # exempt because Trigify can't send Basic auth — it carries its own
    # shared-secret header instead (verified in the handler). Added after CORS so
    # it runs outermost.
    auth_enabled = install_basic_auth(
        app, exempt_paths=("/api/health", "/api/social/trigify"))
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

    def _abm_index() -> AbmIndex | None:
        return getattr(app.state, "abm_index", None)

    def _annotate_panel(companies: list[PanelCompany]) -> list[PanelCompany]:
        """Add measured qualify cost + ABM-target match to each panel company.

        Both are best-effort: a failure here must never break the panel read."""
        try:
            keys = [c.company_key for c in companies]
            costs = app.state.scoring_repo.qualify_costs(keys) if keys else {}
            est = spend_guard.discovery_est_qual_cost()
        except Exception:  # noqa: BLE001 — cost lookup must not break the panel
            logger.exception("panel qualify cost lookup failed")
            costs, est = {}, None
        index = _abm_index()
        out: list[PanelCompany] = []
        for c in companies:
            cost = costs.get(c.company_key)
            states = states_from_locations(s.location for s in (c.signals or []))
            out.append(c.model_copy(update={
                "qualify_cost_usd": cost if cost is not None
                else (est if c.qualified_at else None),
                "abm_match": match_one(index, name=c.name, domain=c.domain, states=states),
            }))
        return out

    def _annotate_scored(rows: list[dict]) -> list[dict]:
        """Stamp each scored-account row with its ABM-target match, by name + domain.

        Only Discovery-sourced accounts are matched: a CSV import comes straight
        from the ABM sheet, so it's a target by definition and the tag would be
        noise — the badge is meant to flag a company we found *independently* that
        turns out to be on the list. Scored accounts don't retain signal geography,
        so matching is domain-first (domain → confirmed; name-only → review) — the
        same index and precision model as the panel, without state corroboration.
        Every row still gets an `abm_match` key (None when not matched / not a
        discovery account / no list), mirroring the panel's shape. Non-mutating:
        returns shallow copies, so the repo's own rows are never touched."""
        index = _abm_index()
        out: list[dict] = []
        for row in rows:
            match = (match_one(index, name=row.get("name"), domain=row.get("domain"))
                     if row.get("source") == "discovery" else None)
            out.append({**row, "abm_match": match.model_dump() if match else None})
        return out

    # ── reads ──────────────────────────────────────────────────────────

    @app.get("/api/stats", response_model=DiscoveryStats)
    def get_stats():
        return svc(app).stats()

    @app.get("/api/panel", response_model=list[PanelCompany])
    def get_panel(
        status: str = "qualified",
        segment: str | None = None,
        signal_type: str | None = None,
        abm: str | None = None,    # "confirmed" | "match" -> filter to ABM-target hits
    ):
        # `status` selects the tab: qualified (default) / needs_review.
        statuses = ("needs_review",) if status == "needs_review" else ("qualified",)
        companies = _annotate_panel(svc(app).list_panel(
            statuses=statuses, segment=segment, signal_type=signal_type))
        if abm == "confirmed":
            companies = [c for c in companies
                         if c.abm_match and c.abm_match.tier == "confirmed"]
        elif abm in ("match", "any", "1", "true"):
            companies = [c for c in companies if c.abm_match]
        return companies

    @app.get("/api/abm/summary")
    def abm_summary():
        """Target-list size + breakdown, and how many rows are indexed live."""
        repo = app.state.repo
        summary = (repo.abm_targets_summary()
                   if hasattr(repo, "abm_targets_summary")
                   else {"total": 0, "by_segment": {}, "uploaded_at": None})
        index = _abm_index()
        summary["indexed"] = index.size if index else 0
        return summary

    @app.post("/api/abm/import")
    async def abm_import(request: Request):
        """Upload the ABM target workbook (.xlsx as raw bytes); replaces the list."""
        data = await request.body()
        if not data:
            raise HTTPException(status_code=400, detail="empty upload")
        try:
            targets = parse_workbook(data)
        except Exception as e:  # noqa: BLE001 — surface a clean 400, not a 500
            logger.exception("ABM workbook parse failed")
            raise HTTPException(
                status_code=400, detail=f"could not parse workbook: {e}") from e
        if not targets:
            raise HTTPException(
                status_code=400, detail="no target accounts found in the workbook")
        stored = app.state.repo.replace_abm_targets([t.model_dump() for t in targets])
        app.state.abm_index = AbmIndex(targets)
        return {"stored": stored, "summary": app.state.repo.abm_targets_summary()}

    @app.get("/api/abm/matches", response_model=list[PanelCompany])
    def abm_matches():
        """Panel companies (qualified + pending) that are on the ABM target list.

        Confirmed matches first, then 'review' (name-only) matches."""
        matched = [c for c in _annotate_panel(svc(app).list_panel()) if c.abm_match]
        matched.sort(
            key=lambda c: 0 if c.abm_match and c.abm_match.tier == "confirmed" else 1)
        return matched

    @app.post("/api/social/trigify")
    async def trigify_webhook(request: Request):
        """Inbound webhook for Trigify social-listening workflows.

        Each enriched engager (one object, or a `{"engagers": [...]}` / bare-array
        batch) is filtered — decision-maker (Director & above), not a Magical
        employee, and for events a confirmed attendee — then the COMPANY runs
        through the existing discovery qualifier and lands on the panel with a
        social signal. The person is carried as a contact in the signal payload.

        Auth: shared secret in the `X-Trigify-Secret` header vs env
        TRIGIFY_WEBHOOK_SECRET (this route is exempt from Basic auth). Per-record
        errors are reported, never 500 the batch."""
        secret = os.getenv("TRIGIFY_WEBHOOK_SECRET")
        if not secret:
            raise HTTPException(status_code=503, detail="social webhook not configured")
        if not secrets.compare_digest(request.headers.get("X-Trigify-Secret", ""), secret):
            raise HTTPException(status_code=401, detail="invalid webhook secret")

        raw = await request.body()
        try:
            data = json.loads(raw) if raw else None
        except (ValueError, TypeError) as e:
            raise HTTPException(status_code=400, detail="invalid JSON body") from e
        if isinstance(data, dict) and isinstance(data.get("engagers"), list):
            records = data["engagers"]
        elif isinstance(data, list):
            records = data
        elif isinstance(data, dict):
            records = [data]
        else:
            raise HTTPException(
                status_code=400, detail="expected an engager object or list")

        # Cost guard — this is a paid path (each NEW company = an LLM qualify),
        # so it gets the SAME defenses as /api/discovery/run: a per-request cap
        # on new qualifications, a monthly-budget pre-check, and a spend Operation
        # so every qualify is recorded in the discovery cost meter. Appending a
        # signal to an already-known company is free and never gated.
        gate, cap, est, _blocked = spend_guard.make_social_gate(app.state.scoring_repo)

        op = spend_guard.Operation(
            app.state.scoring_repo, "social_webhook",
            estimated_usd=round(min(len(records), cap) * est, 4),
            accounts_planned=len(records))
        results: list[dict] = []
        try:
            for rec in records:
                if not isinstance(rec, dict):
                    results.append({"accepted": False, "action": "skipped",
                                    "reason": "invalid_record"})
                    continue
                try:
                    engager = engager_from_trigify(rec)
                except Exception as e:  # noqa: BLE001 — one bad record mustn't 500 the batch
                    results.append({"accepted": False, "action": "skipped",
                                    "reason": f"invalid_payload: {e}"})
                    continue
                try:
                    res = await ingest_engager(
                        engager, repo=app.state.repo, op=op, can_qualify=gate)
                    results.append(res.model_dump())
                except Exception:  # noqa: BLE001 — isolate per-record ingest failures
                    logger.exception("social ingest failed for %r", rec.get("full_name"))
                    results.append({"accepted": False, "action": "error",
                                    "reason": "ingest_error"})
        finally:
            op.finish()
        # Record raw payload + outcome (newest pushed last) so the Trigify field
        # mapping can be inspected via GET /api/social/debug while wiring it up.
        stamp = datetime.now(UTC).isoformat()
        for rec, outcome in zip(records, results, strict=True):
            app.state.social_debug.append({"at": stamp, "raw": rec, "outcome": outcome})
        accepted = sum(1 for r in results if r.get("accepted"))
        qualified_new = sum(1 for r in results if r.get("action") == "qualified")
        return {"received": len(records), "accepted": accepted,
                "skipped": len(records) - accepted,
                "qualified_new": qualified_new, "results": results}

    @app.get("/api/social/debug")
    def social_debug():
        """Last ~50 social-webhook payloads + what we did with each (newest
        first). Behind Basic auth — a wiring aid while mapping Trigify fields."""
        return {"events": list(reversed(app.state.social_debug))}

    # ── monitored LinkedIn accounts (Apify post-engagement) ──────────────────

    @app.get("/api/social/targets")
    def list_social_targets():
        """Monitored accounts whose post engagers we scrape (Magical + competitors)."""
        return {"targets": app.state.repo.social_targets()}

    @app.post("/api/social/targets")
    async def add_social_target(request: Request):
        """Add/update a monitored account: {linkedin_url, label?, kind?, active?}."""
        body = await _json_body(request)
        url = (body.get("linkedin_url") or "").strip()
        # Require a real LinkedIn profile/company host+path (normalize strips
        # scheme/www/regional), so a look-alike like evil.com/linkedin.com/x or a
        # bare host is rejected before it ever reaches the paid scraper.
        if not re.match(r"^linkedin\.com/(in|company|school)/.+", normalize_linkedin_url(url)):
            raise HTTPException(
                status_code=400,
                detail="a LinkedIn profile/company URL is required (linkedin.com/company/… or /in/…)")
        target = SocialTarget(
            linkedin_url=url, label=body.get("label"),
            kind="own" if body.get("kind") == "own" else "competitor",
            active=bool(body.get("active", True)),
        )
        return app.state.repo.upsert_social_target(target.model_dump())

    @app.delete("/api/social/targets")
    async def delete_social_target(request: Request):
        """Remove a monitored account by {linkedin_url}. Magical can't be removed."""
        body = await _json_body(request)
        url = (body.get("linkedin_url") or "").strip()
        if normalize_linkedin_url(url) == normalize_linkedin_url(_MAGICAL_TARGET["linkedin_url"]):
            raise HTTPException(status_code=400, detail="Magical's own account stays monitored")
        return {"removed": app.state.repo.delete_social_target(url)}

    @app.get("/api/social/keywords")
    def list_event_keywords():
        """Event/conference keywords we search public posts for, to find attendees."""
        return {"keywords": app.state.repo.event_keywords()}

    @app.post("/api/social/keywords")
    async def add_event_keyword(request: Request):
        """Add/update an event keyword: {keyword, label?, active?}."""
        body = await _json_body(request)
        kw = (body.get("keyword") or "").strip()
        if len(kw) < 2:
            raise HTTPException(status_code=400, detail="a keyword (2+ chars) is required")
        return app.state.repo.upsert_event_keyword({
            "keyword": kw, "label": body.get("label"),
            "active": bool(body.get("active", True))})

    @app.delete("/api/social/keywords")
    async def delete_event_keyword(request: Request):
        """Remove an event keyword by {keyword}."""
        body = await _json_body(request)
        return {"removed": app.state.repo.delete_event_keyword((body.get("keyword") or "").strip())}

    @app.post("/api/social/run")
    async def social_run(request: Request):
        """Manual social scan with date-window control — the power-user run.

        Body: {window: "24h"|"week"|"month", scope: "all"|"accounts"|"events"}.
        Scans monitored accounts (likes/comments) AND event keywords for the
        chosen window. Shares the discovery run's control + live banner (one run
        at a time, same pause/resume/cancel). The cron + the main Run button use
        the 24h window automatically; this is where you widen it."""
        if getattr(app.state, "discovery_running", False) or \
                getattr(app.state, "social_running", False):
            return {"started": False, "busy": True}
        body = await _json_body(request)
        window = body.get("window") if body.get("window") in _WINDOW_DAYS else "24h"
        scope = body.get("scope") if body.get("scope") in ("all", "accounts", "events") else "all"

        active = [SocialTarget(**t) for t in app.state.repo.social_targets()
                  if t.get("active", True)]
        keywords = [k["keyword"] for k in app.state.repo.event_keywords()
                    if k.get("active", True) and k.get("keyword")]
        do_accounts = scope in ("all", "accounts") and bool(active)
        do_events = scope in ("all", "events") and bool(keywords)
        if not do_accounts and not do_events:
            return {"started": False, "no_targets": True}

        budget_gate, cap, est, blocked_now = spend_guard.make_social_gate(app.state.scoring_repo)
        if blocked_now:
            return {"started": False, "budget_blocked": True}

        ctrl = app.state.discovery_control
        ctrl.reset()
        app.state.social_running = True
        app.state.run_phase = f"Scanning LinkedIn engagement ({window})"
        since = (datetime.now(UTC) - timedelta(days=_WINDOW_DAYS[window])).isoformat()
        date_filter = _WINDOW_FILTER[window]

        async def _run() -> None:
            op = spend_guard.Operation(app.state.scoring_repo, "social_poll",
                                       estimated_usd=round(cap * est, 4))
            try:
                if do_accounts:
                    app.state.last_social = await poll_targets(
                        active, repo=app.state.repo, op=op, can_qualify=budget_gate,
                        gate=ctrl.gate, posted_limit_date=since, max_enrich=cap)
                if do_events and not ctrl.cancelled:
                    app.state.run_phase = f"Scanning event keywords ({window})"
                    app.state.last_events = await poll_events(
                        keywords, repo=app.state.repo, op=op, can_qualify=budget_gate,
                        gate=ctrl.gate, date_filter=date_filter, max_enrich=cap)
            except Exception:  # noqa: BLE001 — a poll failure must not kill the worker
                logger.exception("social poll failed")
            finally:
                op.finish()
                app.state.social_running = False
                app.state.run_phase = None

        _schedule_coro(app, _run())
        return {"started": True, "window": window,
                "accounts": len(active) if do_accounts else 0,
                "keywords": len(keywords) if do_events else 0}

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
        social_on = bool(getattr(app.state, "social_running", False))
        return {"active": active, "recent": recent,
                # `running` is unified across both run types so the one live
                # banner + pause/cancel controls cover the social scan too.
                "running": bool(getattr(app.state, "discovery_running", False)) or social_on,
                "phase": getattr(app.state, "run_phase", None)
                         or ("Scanning LinkedIn engagement" if social_on else None),
                "paused": ctrl.paused,
                "cancelling": ctrl.cancelled,
                "last_run": getattr(app.state, "last_discovery", None),
                "last_social": getattr(app.state, "last_social", None)}

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
        if getattr(app.state, "discovery_running", False) or \
                getattr(app.state, "social_running", False):
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
        # The unified Run also scans the monitored LinkedIn accounts unless opted
        # out — so there's one Run, not a separate "scan" the operator must hunt for.
        include_social = body.get("include_social", True)
        raw_sources = body.get("sources")
        sources = None
        if isinstance(raw_sources, list) and raw_sources:
            sources = [s for s in raw_sources if s in discovery_runner.BROWSERLESS_SOURCES]
            if not sources:
                raise HTTPException(status_code=400, detail=(
                    "sources must be a subset of "
                    f"{list(discovery_runner.BROWSERLESS_SOURCES)}"))
        # A manual run is NEVER silently unlimited (that is the runaway-spend
        # footgun). An explicit positive limit is honoured; an explicit
        # {"no_cap": true} opts into a deliberate full pull; anything else
        # (missing/blank/invalid) falls back to the safe per-source default.
        raw_limit = body.get("limit")
        if isinstance(raw_limit, int) and raw_limit > 0:
            limit = raw_limit
        elif body.get("no_cap") is True:
            limit = None
        else:
            limit = spend_guard.discovery_manual_default_limit()

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

            def on_prefilter_spend(spend) -> None:
                # Job-qualifier prefilter is a paid call with no company; record
                # it as a 'qualify' cost_event (no company_key) so it lands in the
                # discovery meter without polluting per-company costs.
                op.record(step="qualify", actual_usd=spend.cost_usd,
                          model=spend.model,
                          metadata={"input_tokens": spend.input_tokens,
                                    "output_tokens": spend.output_tokens,
                                    "measured": True, "phase": "job_prefilter"})

            try:
                app.state.run_phase = "Discovering signals"
                summary = await discovery_runner.run_once(
                    app.state.repo, days=1, sources=sources, limit=limit,
                    on_company=on_company, gate=ctrl.gate,
                    on_prefilter_spend=on_prefilter_spend)
                summary["cost_usd"] = round(op.actual, 4)
                app.state.last_discovery = {**summary, "at": datetime.now(UTC).isoformat()}
            except Exception:  # noqa: BLE001 — never crash the loop
                logger.exception("on-demand discovery run failed")
            # Phase 2 of the SAME run: scan monitored accounts (likes/comments) +
            # event keywords for the last 24h. One Run button does everything —
            # connectors + social + events — under one control, one banner, one
            # cost envelope. Skipped if cancelled or {"include_social": false}.
            if include_social and not ctrl.cancelled:
                try:
                    s_gate, cap, _est, blocked = spend_guard.make_social_gate(
                        app.state.scoring_repo)
                    active = [SocialTarget(**t) for t in app.state.repo.social_targets()
                              if t.get("active", True)]
                    keywords = [k["keyword"] for k in app.state.repo.event_keywords()
                                if k.get("active", True) and k.get("keyword")]
                    since = (datetime.now(UTC) - timedelta(hours=24)).isoformat()
                    if active and not blocked:
                        app.state.run_phase = "Scanning LinkedIn engagement"
                        app.state.last_social = await poll_targets(
                            active, repo=app.state.repo, op=op, can_qualify=s_gate,
                            gate=ctrl.gate, posted_limit_date=since, max_enrich=cap)
                    if keywords and not blocked and not ctrl.cancelled:
                        app.state.run_phase = "Scanning event keywords"
                        app.state.last_events = await poll_events(
                            keywords, repo=app.state.repo, op=op, can_qualify=s_gate,
                            gate=ctrl.gate, date_filter="past-24h", max_enrich=cap)
                except Exception:  # noqa: BLE001 — a social failure mustn't fail the run
                    logger.exception("social phase of discovery run failed")
            op.finish()
            app.state.discovery_running = False
            app.state.run_phase = None

        _schedule_coro(app, _run())
        return {"started": True,
                "sources": sources or list(discovery_runner.BROWSERLESS_SOURCES),
                "limit": limit}

    def _run_active() -> bool:
        return bool(getattr(app.state, "discovery_running", False)
                    or getattr(app.state, "social_running", False))

    @app.post("/api/discovery/pause")
    def discovery_pause():
        """Pause the in-flight run (discovery OR social scan) at the next
        boundary — no new paid call starts, so spend freezes until resumed."""
        if not _run_active():
            return {"running": False, "paused": False}
        app.state.discovery_control.pause()
        return {"running": True, "paused": True}

    @app.post("/api/discovery/resume")
    def discovery_resume():
        """Resume a paused run from exactly where it stopped."""
        app.state.discovery_control.resume()
        return {"running": _run_active(), "paused": False}

    @app.post("/api/discovery/cancel")
    def discovery_cancel():
        """Cancel the in-flight run (discovery OR social). It stops cleanly at the
        next boundary (any in-flight call finishes). Re-running later 'smart
        resumes': already-qualified companies are skipped by the dedup ledger, so
        it picks up where it left off without paying twice."""
        app.state.discovery_control.cancel()
        return {"cancelling": True, "running": _run_active()}

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
        The dashboard filters client-side. Each row carries its ABM-target match
        (when a list is loaded) so the scored board badges the same hits the
        discovery panel does."""
        return _annotate_scored(app.state.scoring.list_scored())

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
