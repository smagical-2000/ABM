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
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from auto_search import autoscore, discovery_runner, job_stacking, lifecycle, news, priority
from auto_search.abm import (
    AbmIndex,
    TargetAccount,
    match_one,
    parse_workbook,
    states_from_locations,
)
from auto_search.api.auth import install_basic_auth
from auto_search.clients import cms_aco
from auto_search.db import get_repository
from auto_search.db.engagement_repository import (
    dedupe_contacts,
    engaging_contacts,
    get_engagement_repository,
)
from auto_search.db.scoring_repository import (
    STALE_SCORING_SECONDS,
    get_scoring_repository,
)
from auto_search.engagement import notify as engagement_notify
from auto_search.engagement import scoring as engagement_scoring
from auto_search.engagement import sync as engagement_sync_mod
from auto_search.intros import profiles as intros_profiles
from auto_search.intros import service as intros_service
from auto_search.normalize import clean_domain, normalize_company_name, normalize_linkedin_url
from auto_search.run_control import RunControl
from auto_search.runtime import is_production
from auto_search.scoring import budget as budget_guard
from auto_search.scoring import imports as csv_imports
from auto_search.scoring import lookup as ae_lookup
from auto_search.scoring import spend_guard
from auto_search.scoring.frameworks import FRAMEWORKS, all_frameworks_public, resolve_tier
from auto_search.scoring.service import ScoringService
from auto_search.services import DiscoveryStats, PanelCompany, ReviewService
from auto_search.social import (
    SocialTarget,
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


def _ae_brief_auto() -> bool:
    """One-flow AE brief kill switch — AE_BRIEF_AUTO=0 reverts to score-only
    lookups (manual Generate button still works) without a redeploy."""
    return os.getenv("AE_BRIEF_AUTO", "1") != "0"


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
            saved = await app.state.scoring.run_scoring(account_id, op=op)
        finally:
            op.finish()
            app.state.scoring_inflight.discard(account_id)
        # One-flow AE brief: an AE lookup continues straight into the full
        # dossier so the AE lands on ONE readable brief, not a bare score.
        # Revertible without a redeploy: set AE_BRIEF_AUTO=0 to turn off.
        # Guarded like the manual button: scored + budget + not in flight.
        if (op_type == "ae_lookup" and _ae_brief_auto()
                and saved and saved.get("state") == "scored"
                and saved.get("dossier_state") != "generating"):
            try:
                budget_guard.assert_affordable(app.state.scoring_repo.cost_summary(),
                                               budget_guard.EST_DOSSIER_COST)
            except budget_guard.BudgetExceeded:
                logger.info("AE brief skipped for %s — monthly budget reached", account_id)
                return
            app.state.scoring_repo.set_dossier_state(account_id, "generating")
            dop = spend_guard.Operation(app.state.scoring_repo, "dossier",
                                        estimated_usd=budget_guard.EST_DOSSIER_COST,
                                        accounts_planned=1)
            try:
                await app.state.scoring.generate_dossier(account_id, op=dop)
            finally:
                dop.finish()

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


def _lookup_engagement_context(rows: list[dict], name: str, website: str | None) -> dict | None:
    """Engagement garnish for the lookup card: if the typed company is already
    engaging (Reply.io/SFDC/podcast/LinkedIn heat), say so. `rows` are the
    SHAPED engagement-board rows (the app's _engaged_view() — name/tier/score
    resolved), NOT the raw engaged_accounts view, which carries only counters."""
    from auto_search.clients.exa import domain_of
    dom = domain_of(website)
    key = normalize_company_name(name)
    for a in rows or []:
        if (dom and (a.get("domain") or "").lower() == dom) or \
                (key and normalize_company_name(a.get("name") or "") == key):
            return {"account_id": a.get("account_id"), "tier": a.get("tier"),
                    "heat": a.get("score"), "last_touch": a.get("last_touch")}
    return None


def _engagement_intent_signals(engagement_repo, name: str,
                               engagement_account_id: str | None = None) -> list[dict]:
    """First-party intent for the fit scorer: the account's engagement events
    (booked meetings, BOFU forms, ad engagement) as carried signals. Matches by
    the shared normalized-name key (engagement ids are abm_/acc_ + key) or an
    explicit id from the engagement-context match. Best-effort: [] on anything."""
    if engagement_repo is None:
        return []
    from auto_search.engagement import intent_feed
    key = normalize_company_name(name)
    candidates = [engagement_account_id] if engagement_account_id else []
    candidates += [f"abm_{key}", f"acc_{key}"] if key else []
    try:
        for aid in candidates:
            events = engagement_repo.events_for_account(aid)
            if events:
                return intent_feed.to_intent_signals(events)
    except Exception:  # noqa: BLE001 — intent garnish must never block a lookup
        logger.exception("engagement intent signals failed for %s", name)
    return []


def _classify_import_row(name, account_id, *, get_company, exists):
    """Decide what to do with one imported CSV row, by company IDENTITY — not the
    import's own id scheme, which is exactly why duplicates slipped through:

      "skip"  already scored — promoted from discovery ("acc_"+key) or a prior CSV
      "move"  a live discovery company → promote it into Scored WITH its signals
              (it leaves the Discovery panel) instead of making a signal-less twin
      "new"   not seen → create a fresh CSV account

    `get_company(key) -> PanelCompany|None` and `exists(account_id) -> bool` are
    injected so this is unit-testable without the app. Returns (action, company).
    """
    key = normalize_company_name(name)
    if exists("acc_" + key) or exists(account_id):
        return "skip", None
    company = get_company(key)
    if company is not None and getattr(company, "icp_status", None) in ("qualified", "needs_review"):
        return "move", company
    return "new", None


# Upload caps: a CSV import is raw-bodied, so bound it to avoid an OOM body or a
# runaway queue that a later "Score all" could turn into a big spend.
_MAX_UPLOAD_BYTES = 5_000_000        # 5 MB (CSV imports)
_MAX_XLSX_BYTES = 10_000_000         # 10 MB (ABM workbook — openpyxl loads it all)
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
    """Ensure Magical's own account + the known competitors are monitored (idempotent).
    Competitors feed both the social poll and the competitor news monitor; more can
    be added in-platform via POST /api/social/targets."""
    if not hasattr(repo, "upsert_social_target"):
        return
    try:
        from auto_search.news.competitors import COMPETITORS
        existing = {normalize_linkedin_url(t.get("linkedin_url"))
                    for t in repo.social_targets()}
        seeds = [_MAGICAL_TARGET] + [
            {"linkedin_url": c["linkedin_url"], "label": c.get("label"),
             "kind": "competitor", "active": True} for c in COMPETITORS]
        for seed in seeds:
            if normalize_linkedin_url(seed["linkedin_url"]) not in existing:
                repo.upsert_social_target(dict(seed))
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

    def _own_signals(account) -> list[dict]:
        """First-party engagement intent, merged into EVERY score at score time
        (ae lookups, promotes, re-scores, batches) — so no score can claim "no
        intent signals" about an account that engaged with us. The full
        provider (with the shaped-board domain fallback) is published by
        create_app on app.state; the name-key probe is the fallback here."""
        provider = getattr(app.state, "own_signals_provider", None)
        if provider is not None:
            return provider(account)
        return _engagement_intent_signals(
            getattr(app.state, "engagement_repo", None), account.name)

    app.state.scoring = ScoringService(scoring_repo, own_signals=_own_signals)
    app.state.repo = repo
    app.state.scoring_repo = scoring_repo
    # Engagement store (Reply.io heat). Additive + isolated — never block startup.
    try:
        app.state.engagement_repo = get_engagement_repository()
        app.state.engagement_repo.ensure_schema()
    except Exception:  # noqa: BLE001
        logger.exception("engagement init failed")
        app.state.engagement_repo = None
    app.state.engagement_running = False           # one sync at a time
    app.state.abm_index = _load_abm_index(repo)   # ABM target list -> match index
    _seed_social_targets(repo)                    # ensure Magical is always monitored
    app.state.social_running = False              # one social poll at a time
    app.state.news_running = False                # one news refresh at a time
    app.state.last_news = None
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
    # Same sweep for spend operations: ops finish in a finally, so a row still
    # 'running' at boot was orphaned by a crash/restart mid-run. Fail it so the
    # ops feed can't show phantom in-flight spend forever.
    stale_ops = scoring_repo.fail_orphaned_operations()
    if stale_ops:
        logger.warning("failed %d orphaned spend operation(s) from a prior process",
                       stale_ops)
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
    # /api/health is exempt for the platform healthcheck. Added after CORS so it
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

    def _abm_index() -> AbmIndex | None:
        return getattr(app.state, "abm_index", None)

    def _engagement_tiers() -> tuple[dict, dict]:
        """(by_domain, by_company_key) -> engagement heat tier, for the AGT-1390
        Discovery score lift (an engaged account ranks higher). Best-effort."""
        repo = getattr(app.state, "engagement_repo", None)
        if not repo:
            return {}, {}
        try:
            tier_by_acct = {a["account_id"]: engagement_scoring.tier_for(a.get("score") or 0)
                            for a in repo.engaged_accounts()}
            by_dom, by_key = {}, {}
            for c in repo.contacts():
                t = tier_by_acct.get(c.get("account_id"))
                if not t:
                    continue
                if c.get("email_domain"):
                    by_dom.setdefault(c["email_domain"], t)
                if c.get("company_key"):
                    by_key.setdefault(c["company_key"], t)
            return by_dom, by_key
        except Exception:  # noqa: BLE001 — never break the panel read
            logger.exception("engagement tier lookup failed")
            return {}, {}

    def _abm_social_lookup(name, domain=None):
        """Is this company on the ABM target list? The social flow uses it to
        treat a tracked account as authoritative — surfaced + highlighted without
        paying the ICP qualifier (the list IS the qualification)."""
        return match_one(_abm_index(), name=name, domain=domain)

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
        eng_dom, eng_key = _engagement_tiers()   # AGT-1390: engaged accounts rank higher
        out: list[PanelCompany] = []
        for c in companies:
            cost = costs.get(c.company_key)
            states = states_from_locations(s.location for s in (c.signals or []))
            abm_match = match_one(index, name=c.name, domain=c.domain, states=states)
            sigs = [{"signal_type": s.signal_type, "title": s.title, "role": s.role,
                     "tier": s.tier, "observed_at": s.observed_at} for s in (c.signals or [])]
            eng_tier = eng_dom.get(clean_domain(c.domain)) or eng_key.get(c.company_key)
            it = priority.intent(
                sigs, abm_confirmed=bool(abm_match and abm_match.tier == "confirmed"),
                outcomes={"engagement_tier": eng_tier} if eng_tier else None)
            # Lone standard hire (single biller/coder, nothing stronger) → Watch list,
            # not Discovery. Same gate the cron parks on, so the views agree.
            is_watchlist = job_stacking.should_park_flat(sigs)
            # When this lead will auto-move (for the panel TTL badge). Only pending
            # leads decay; the math lives in lifecycle so the badge matches the sweep.
            ttl_action, ttl_days = (None, None)
            if c.review_status == "pending":
                ttl_action, ttl_days = lifecycle.next_transition(
                    icp_status=c.icp_status, tier=it.tier,
                    signals=sigs,
                    entered_review_at=c.entered_review_at)
            out.append(c.model_copy(update={
                "qualify_cost_usd": cost if cost is not None
                else (est if c.qualified_at else None),
                "abm_match": abm_match,
                "intent_score": it.score, "intent_tier": it.tier, "intent_reason": it.reason,
                "ttl_action": ttl_action, "ttl_days": ttl_days,
                "is_watchlist": is_watchlist,
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
            out.append(_retier({**row, "abm_match": match.model_dump() if match else None}))
        return out

    def _resolve_scored_band(scored: dict):
        """The Band a scored row resolves to under TODAY's rubric (so a rubric change
        renders consistently without a re-score). None when the framework is unknown or
        the total is missing. `scored['framework']` is the raw key (health_system/…)."""
        fw = FRAMEWORKS.get((scored or {}).get("framework"))
        if fw is None or (scored or {}).get("total") is None:
            return None
        return resolve_tier(fw, scored["total"], (scored or {}).get("dimensions"))

    def _retier(row: dict) -> dict:
        """Re-resolve the tier + max_total against the CURRENT framework so accounts
        scored under an older rubric (e.g. HS 27->30 and the new bands) render
        consistently without a re-score. Rewrites BOTH the flat keys AND the nested
        `tier` object the UI actually reads. The stored `total` is unchanged (a true
        re-score may nudge it for HS, where a dimension max moved). No-op when
        unresolvable."""
        band = _resolve_scored_band(row)
        if band is None:
            return row
        fw = FRAMEWORKS[row["framework"]]
        return {**row, "tier_band": band.band, "tier_label": band.label,
                "tier": {"band": band.band, "label": band.label},
                "max_total": fw.max_total}

    # ── reads ──────────────────────────────────────────────────────────

    @app.get("/api/stats", response_model=DiscoveryStats)
    def get_stats():
        return svc(app).stats()

    def _belongs_on_watchlist(c: PanelCompany) -> bool:
        """A lone standard-hire that should sit on the Watch list, not the main
        Discovery panel — UNLESS it's a confirmed ABM target, which we always
        surface (a tracked account matters regardless of hiring intent)."""
        confirmed_abm = bool(c.abm_match and c.abm_match.tier == "confirmed")
        return c.is_watchlist and not confirmed_abm

    @app.get("/api/panel", response_model=list[PanelCompany])
    def get_panel(
        status: str = "qualified",
        segment: str | None = None,
        signal_type: str | None = None,
        abm: str | None = None,    # "confirmed" | "match" -> filter to ABM-target hits
        watchlist: str | None = None,  # ""/None = Discovery (hide watch-list); "only" = the watch list
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
        # Discovery shows real-intent leads; lone standard hires live on the Watch
        # list. Only split the qualified tab — needs_review is its own lifecycle.
        if status == "qualified":
            keep = _belongs_on_watchlist if watchlist == "only" else \
                (lambda c: not _belongs_on_watchlist(c))
            companies = [c for c in companies if keep(c)]
        companies.sort(key=lambda c: -c.intent_score)   # hottest intent first
        return companies

    @app.post("/api/discovery/sweep")
    def discovery_sweep():
        """Run one self-cleaning lifecycle pass: cold Watch -> Needs review,
        re-heated (Hot) -> promoted back to Discovery, in-review-too-long ->
        auto-rejected. ABM-aware so Hot matches the panel. Re-heated leads are
        auto-scored in the background (budget-permitting); an auto-reject restores
        like any manual reject."""
        result = lifecycle.sweep(app.state.repo, abm_index=_abm_index())
        if autoscore.autoscore_enabled():
            for key in result.promoted_keys:
                company = svc(app).get_company(key)
                if company is None:
                    continue
                summary = app.state.scoring_repo.cost_summary()
                if budget_guard.remaining(summary) < budget_guard.EST_SCORE_COST:
                    continue                 # no headroom — leave it qualified-Hot
                svc(app).promote(key)
                row = app.state.scoring.enqueue_discovery(
                    company.model_dump(), state="scoring")
                _schedule_scoring(app, row["account_id"], op_type="promote")
        return result.as_dict()

    # ── market-intelligence news (RCM / regulation headlines) ────────

    @app.get("/api/news")
    def get_news(topic: str | None = None, days: int = 30, limit: int = 200):
        """RCM / regulation headlines for the News tab, ranked by get-behind (how
        hard Magical should act on each as an outreach wedge), newest breaking ties."""
        repo = app.state.repo
        base = {"topics": list(news.TOPICS), "labels": news.TOPIC_LABELS,
                "last_run": getattr(app.state, "last_news", None)}
        if not hasattr(repo, "news_items"):
            return {"items": [], **base}
        topics = (topic,) if topic in news.TOPICS else None
        return {"items": repo.news_items(topics=topics, days=days, limit=limit), **base}

    @app.post("/api/news/refresh")
    def news_refresh(reenrich: bool = False):
        """Pull the latest headlines + tag them in the background. One at a time.
        `reenrich=true` re-tags the already-stored feed instead (a one-off backfill
        after the enrich model gains fields)."""
        if getattr(app.state, "news_running", False):
            return {"started": False, "busy": True}
        app.state.news_running = True

        async def _run() -> None:
            op = spend_guard.Operation(app.state.scoring_repo, "news_refresh",
                                       estimated_usd=0.0, accounts_planned=0)

            def on_cost(usd: float) -> None:
                op.record(step="news_enrich", actual_usd=usd, model="news")

            try:
                summary = await (news.reenrich_stored(app.state.repo, on_cost=on_cost)
                                 if reenrich
                                 else news.run_once(app.state.repo, on_cost=on_cost))
                app.state.last_news = {**summary, "at": datetime.now(UTC).isoformat()}
            except Exception:  # noqa: BLE001 — never crash the loop
                logger.exception("news refresh failed")
            finally:
                op.finish()
                app.state.news_running = False

        _schedule_coro(app, _run())
        return {"started": True}

    @app.post("/api/news/competitors/run")
    def news_competitors_run():
        """Scan the monitored competitors for distress / negative press and store
        the hits as news_items (topic 'Competitor: <name>') with the fast-follower
        play. Free (Google News RSS); shares the news_running lock."""
        if getattr(app.state, "news_running", False):
            return {"started": False, "busy": True}
        app.state.news_running = True

        async def _run() -> None:
            from auto_search.news import competitors
            try:
                summary = await competitors.run_competitor_news(app.state.repo)
                app.state.last_competitor_news = {
                    **summary, "at": datetime.now(UTC).isoformat()}
            except Exception:  # noqa: BLE001 — never crash the loop
                logger.exception("competitor news run failed")
            finally:
                app.state.news_running = False

        _schedule_coro(app, _run())
        return {"started": True}

    # ── engagement (Reply.io heat) ──────────────────────────────────────────────

    # ABM-import artifacts that are sheet/tab names, not classifications — Unclassified.
    _JUNK_SEGMENTS = frozenset({"Matches", "Sheet30"})
    _FRAMEWORK_LABEL = {"specialty": "Specialty", "health_system": "Health System",
                        "payer": "Payer"}

    def _clean_segment(seg):
        """Normalize an ABM segment to a clean label; junk (sheet names) -> None.
        The workbook truncated 'Specialties (Definitive, 20,000...' and abbreviates
        physician groups as 'PGs - X' — fix both so they read as real classes."""
        s = (seg or "").strip()
        if not s or s in _JUNK_SEGMENTS:
            return None
        if s.startswith("Specialties (Definitive"):
            return "Specialties"
        if s.startswith("PGs - "):
            return "Physician Group - " + s[len("PGs - "):]
        return s

    def _classify(scored: dict, abm: dict, ai_framework: str | None = None) -> dict:
        """Classification shown on an engaged account: the scored system's framework +
        fit tier (authoritative) AND the segment (scored's, else cleaned ABM's). The
        junk ABM segments collapse to None (the 'Unclassified' fix).

        `ai_framework` is the Claude classification fallback for engaged-but-never-scored
        accounts — used ONLY when there's no scored framework, and ONLY for routable
        buckets (health_system/payer/specialty), so a guess never overrides real scoring."""
        fw = (scored or {}).get("framework")
        if not fw and ai_framework in ("health_system", "payer", "specialty"):
            fw = ai_framework
        fw_label = _FRAMEWORK_LABEL.get(fw, fw) if fw else None
        seg = _clean_segment((scored or {}).get("segment") or (abm or {}).get("segment"))
        # Re-resolve the fit tier against today's rubric (don't trust the stored label,
        # which is stale after a band change) — keeps the Slack card / Activity view in
        # step with the Scored board.
        band = _resolve_scored_band(scored)
        fit_tier = band.label if band is not None else (scored or {}).get("tier_label")
        return {
            "framework": fw_label,
            "framework_key": fw,        # raw key (health_system/…) for AE routing
            "fit_tier": fit_tier,
            # display class: scored framework label (clean) over a raw lowercase segment
            "segment": fw_label or seg,
        }

    def _momentum(series: list[int] | None, weeks: int = 8) -> tuple[list[int], str, int]:
        """(series, trend, delta_week) for the console sparkline. trend compares the
        last 2 weeks vs the prior 2; delta_week is the most recent week's points."""
        s = list(series) if series else [0] * weeks
        if len(s) < weeks:
            s = [0] * (weeks - len(s)) + s
        delta_week = s[-1]
        recent, prior = sum(s[-2:]), sum(s[-4:-2])
        trend = "up" if recent > prior else "down" if recent < prior else "flat"
        return s, trend, delta_week

    def _abm_display(discovery_repo) -> dict[str, dict]:
        """account_id -> display info for ABM-only engaged accounts (abm_<key>)."""
        from auto_search.normalize import normalize_company_name
        out: dict[str, dict] = {}
        targets = (discovery_repo.abm_targets()
                   if hasattr(discovery_repo, "abm_targets") else [])
        for t in targets:
            key = normalize_company_name(t.get("name") or "")
            if not key:
                continue
            aid = f"abm_{key}"
            rec = out.get(aid)
            if rec is None:
                rec = out[aid] = {"name": t.get("name"), "segment": None, "domain": None}
            # duplicate rows per company are common (one carries the real class, one
            # the junk 'Matches' tab) — keep the first NON-junk segment + a domain.
            if rec["segment"] is None:
                rec["segment"] = _clean_segment(t.get("segment"))
            rec["domain"] = rec["domain"] or t.get("domain")
            rec["name"] = rec["name"] or t.get("name")
        return out

    # touches that don't, on their own, make an account worth resurfacing on the
    # Activity view — an email click isn't "they moved" (mirrors the lean bar the
    # user/Galyna asked for: a real worklist, not noise). A TOFU form lead now
    # scores 6 (a real lead), so it surfaces like the other meaningful touches.
    _ACTIVITY_NOISE = frozenset({"click"})

    def _recent_touch_by_account(repo, *, days: int = 14) -> dict[str, dict]:
        """account_id -> the most significant MEANINGFUL touch in the last `days`
        ({kind, at}). Powers the Activity view's "what changed" + recency."""
        from auto_search.db.engagement_repository import _parse_iso
        cutoff = datetime.now(UTC) - timedelta(days=days)
        best: dict[str, dict] = {}
        for e in repo.recent_events(limit=5000):
            aid = e.get("account_id")
            if not aid or e.get("kind") in _ACTIVITY_NOISE:
                continue
            ts = _parse_iso(e.get("occurred_at"))
            if not ts or ts < cutoff:
                continue
            pts = int(e.get("points") or 0)
            cur = best.get(aid)
            if cur is None or pts > cur["_pts"] or (pts == cur["_pts"] and ts > cur["_ts"]):
                best[aid] = {"kind": e.get("kind"), "at": e.get("occurred_at"),
                             "_pts": pts, "_ts": ts}
        return {aid: {"kind": v["kind"], "at": v["at"]} for aid, v in best.items()}

    def _engaged_view() -> list[dict]:
        """Engaged accounts ranked by heat, enriched with display info, tier + rates."""
        repo = getattr(app.state, "engagement_repo", None)
        if not repo:
            return []
        lists_by: dict[str, set] = {}
        for c in repo.contacts():
            aid = c.get("account_id")
            if aid:
                lists_by.setdefault(aid, set()).update(c.get("matched_lists") or [])
        scored = {a["account_id"]: a for a in app.state.scoring.list_scored()}
        abm = _abm_display(app.state.repo)
        series_by = repo.account_weekly_series(weeks=8)
        recent_by = _recent_touch_by_account(repo)
        activated = repo.activated_account_ids()   # which accounts a rep already actioned
        import json as _json
        ai_map = _json.loads(repo.get_setting("ai_classifications") or "{}")  # Claude fallbacks
        out: list[dict] = []
        for r in repo.engaged_accounts():
            aid = r["account_id"]
            s = scored.get(aid) or {}
            d = abm.get(aid, {})
            delivered = r.get("delivered") or 0
            series, trend, delta_week = _momentum(series_by.get(aid))
            out.append({
                **r,
                "name": s.get("name") or d.get("name") or aid,
                **_classify(s, d, ai_map.get(aid)),
                "domain": s.get("domain") or d.get("domain"),
                "tier": engagement_scoring.tier_for(r.get("score") or 0),
                "activated": aid in activated,   # show "Activated" so reps don't redo it
                "lists": sorted(lists_by.get(aid, [])),
                "abm": "abm" in lists_by.get(aid, set()),
                "series": series, "trend": trend, "delta_week": delta_week,
                "recent": recent_by.get(aid),
                "open_rate": (round(100 * (r.get("opened") or 0) / delivered)
                              if delivered else None),
                "reply_rate": (round(100 * (r.get("replied_sends") or 0) / delivered)
                               if delivered else None),
            })
        out.sort(key=lambda x: (x.get("score") or 0, x.get("last_touch") or ""), reverse=True)
        return out

    def _own_signals_provider(account) -> list[dict]:
        """The full own-signals provider for ScoringService (published on
        app.state; the lifespan wires it in). Match order: normalized-name key
        (abm_/acc_ + key), then DOMAIN via the shaped board — names drift (AKA
        suffixes, csv_ imports; the Riverview case), domains don't."""
        repo = getattr(app.state, "engagement_repo", None)
        signals = _engagement_intent_signals(repo, account.name)
        if not signals and account.domain:
            try:
                ctx = _lookup_engagement_context(_engaged_view(), account.name,
                                                 account.domain)
            except Exception:  # noqa: BLE001 — garnish; never block scoring
                logger.exception("own-signals domain match failed for %s", account.name)
                ctx = None
            if ctx and ctx.get("account_id"):
                signals = _engagement_intent_signals(repo, account.name,
                                                     ctx["account_id"])
        return signals

    app.state.own_signals_provider = _own_signals_provider

    @app.get("/api/engagement")
    def get_engagement():
        repo = getattr(app.state, "engagement_repo", None)
        last_sync = repo.get_sync_state() if repo else None
        return {"accounts": _engaged_view(), "last_sync": last_sync,
                "running": bool(getattr(app.state, "engagement_running", False))}

    # NOTE: defined before /{account_id} so "export.csv"/"inbox" aren't captured as ids.
    @app.get("/api/engagement/export.csv")
    def engagement_export_csv():
        """Download the engaged-account board as CSV — one row per account with the
        full intent payload, for sales to work in a sheet."""
        import csv
        import io
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["Account", "Domain", "Classification", "Fit tier", "Lists",
                    "Heat tier", "Score", "Trend", "Δ this week", "Contacts",
                    "Clicks", "Replies", "Meetings", "Open rate %", "Reply rate %",
                    "Last touch"])
        for a in _engaged_view():
            w.writerow([
                a.get("name"), a.get("domain") or "",
                a.get("framework") or a.get("segment") or "", a.get("fit_tier") or "",
                " + ".join(a.get("lists") or []), a.get("tier"), a.get("score"),
                a.get("trend"), a.get("delta_week"), a.get("contacts"),
                a.get("clicks"), a.get("replies"), a.get("meetings"),
                "" if a.get("open_rate") is None else a.get("open_rate"),
                "" if a.get("reply_rate") is None else a.get("reply_rate"),
                (a.get("last_touch") or "")[:10],
            ])
        return Response(content=buf.getvalue(), media_type="text/csv",
                        headers={"Content-Disposition":
                                 "attachment; filename=magical-engagement.csv"})

    @app.get("/api/engagement/inbox")
    def get_engagement_inbox(limit: int = 200):
        """Recent meaningful touches across all accounts (the Inbox feed) +
        the unresolved-contact count."""
        repo = getattr(app.state, "engagement_repo", None)
        if not repo:
            return {"events": [], "unresolved": 0}
        scored = {a["account_id"]: a for a in app.state.scoring.list_scored()}
        abm = _abm_display(app.state.repo)
        all_contacts = repo.contacts()
        tier_by_contact = {c["external_id"]: c.get("match_tier") for c in all_contacts}
        events = []
        for e in repo.recent_events(limit=limit):
            aid = e.get("account_id")
            disp = scored.get(aid) or abm.get(aid) or {}
            events.append({
                "kind": e.get("kind"), "channel": e.get("channel"),
                "points": e.get("points"), "company": e.get("company"),
                "account_id": aid, "account_name": disp.get("name"),
                "campaign": e.get("campaign"), "occurred_at": e.get("occurred_at"),
                "match_tier": tier_by_contact.get(e.get("contact_ext")),
            })
        return {"events": events,
                "unresolved": sum(1 for c in all_contacts if not c.get("account_id"))}

    def _engaged_one(account_id, events, contacts):
        """Single-account rollup for the drawer — from the rows already fetched, so
        opening a drawer doesn't recompute the whole board."""
        allc = dedupe_contacts(contacts)                 # all recipients, for rate math
        engaged = engaging_contacts(contacts, events)    # only people who actually engaged
        score = sum(e.get("points") or 0 for e in events)
        delivered = sum(c.get("delivered") or 0 for c in allc)
        opened = sum(c.get("opened") or 0 for c in allc)
        replied = sum(c.get("replied") or 0 for c in allc)
        s = {}
        if hasattr(app.state.scoring_repo, "get"):
            s = app.state.scoring_repo.get(account_id) or {}
        d = (_abm_display(app.state.repo).get(account_id, {})
             if account_id.startswith("abm_") else {})
        import json as _json
        _repo = getattr(app.state, "engagement_repo", None)
        _ai = _json.loads(_repo.get_setting("ai_classifications") or "{}") if _repo else {}
        return {
            "account_id": account_id,
            "name": s.get("name") or d.get("name") or account_id,
            **_classify(s, d, _ai.get(account_id)),
            "domain": s.get("domain") or d.get("domain"),
            "score": score, "tier": engagement_scoring.tier_for(score),
            "clicks": sum(1 for e in events if e.get("kind") == "click"),
            "replies": sum(1 for e in events if e.get("kind") == "reply"),
            "meetings": sum(1 for e in events if e.get("kind") == "meeting_booked"),
            "contacts": len(engaged), "delivered": delivered, "opened": opened,
            "replied_sends": replied,
            "last_touch": max((e.get("occurred_at") for e in events if e.get("occurred_at")),
                              default=None),
            "lists": sorted({x for c in allc for x in (c.get("matched_lists") or [])}),
            "open_rate": round(100 * opened / delivered) if delivered else None,
            "reply_rate": round(100 * replied / delivered) if delivered else None,
        }

    @app.get("/api/engagement/{account_id}")
    def get_engagement_account(account_id: str):
        repo = getattr(app.state, "engagement_repo", None)
        if not repo:
            raise HTTPException(status_code=404, detail="engagement not available")
        events = repo.events_for_account(account_id)
        raw = repo.contacts(account_id=account_id)
        if not events and not raw:
            raise HTTPException(status_code=404, detail="account not found")
        # avatars = the people who actually engaged (deduped), matching the count
        return {"account": _engaged_one(account_id, events, raw),
                "events": events, "contacts": engaging_contacts(raw, events)}

    @app.post("/api/engagement/{account_id}/activate")
    async def engagement_activate(account_id: str, request: Request):
        """Activate an account → enrich it (decision-makers + verified email/mobile
        via Apollo + FullEnrich) and post a full sales packet (intent story + heat +
        contacts) to the Slack engagement channel. Enrichment runs ONLY here (on
        activation), so credits are spent only on accounts a rep chose to action.
        Body: {"test": true} marks a wiring test (always posts, never deduped);
        {"force": true} deliberately re-activates an already-activated account.

        Multi-user safe: a non-test activation atomically CLAIMS the account first,
        so two reps (or the auto-route loop across browsers) clicking Activate fire
        it exactly once — the loser gets {"already_activated": true} with no spend."""
        repo = getattr(app.state, "engagement_repo", None)
        if not repo:
            raise HTTPException(status_code=503, detail="engagement store not available")
        events = repo.events_for_account(account_id)
        contacts = repo.contacts(account_id=account_id)
        if not events and not contacts:
            raise HTTPException(status_code=404, detail="account not found")
        body = await _json_body(request)
        is_test = bool(body.get("test"))
        force = bool(body.get("force"))
        # Send cutoff (Galyna, 2026-06-25): we capture ALL history but only hand off
        # activity dated on/after the cutoff, so the team is never sent the already-
        # processed backlog. Exempt: an explicit {"force": true} override and {"test": true}
        # posts (private channel). Checked BEFORE the dedup claim, so a suppressed account
        # is NOT marked activated and can still send later if it genuinely re-engages.
        cutoff = (repo.get_setting("activation_cutoff") or "").strip()[:10]
        if cutoff and not is_test and not force:
            last_touch = max((e.get("occurred_at") or "" for e in events), default="")
            if last_touch[:10] < cutoff:
                return {"posted": False, "suppressed": True, "reason": "before_cutoff",
                        "cutoff": cutoff, "last_touch": last_touch or None,
                        "account_id": account_id}
        # Server-side dedup: claim before any paid/visible work. The winner proceeds;
        # everyone else short-circuits. `force` re-activates on purpose; `test` is exempt.
        claimed = False
        if not is_test:
            claimed = repo.claim_activation(account_id)
            if not claimed and not force:
                return {"posted": False, "already_activated": True, "account_id": account_id}
        # Everything after the claim runs under one guard: if ANY step fails (not just
        # the Slack post), release the claim so the account isn't left stuck
        # "activated" with no post — a retry can then re-activate it.
        try:
            account = _engaged_one(account_id, events, contacts)
            # SDR intel brief — reuse the scored account's already-stored research
            # (discovery triggers + Claude dossier). Free, instant, no live call. It's
            # a non-critical garnish, so a DB hiccup here must never block activation.
            research: dict = {}
            try:
                scored = (app.state.scoring_repo.get(account_id)
                          if hasattr(app.state.scoring_repo, "get") else None)
                research = engagement_notify.summarize_research(scored)
            except Exception:  # noqa: BLE001 — intel is optional; activation must not 500
                logger.exception("intel brief failed for %s", account_id)
            tier = account.get("tier") or "—"
            is_hot = tier == "Hot"
            is_sdr_tier = tier in ("Warm", "Some")   # SDRs own Warm + Some
            notify = is_hot or is_sdr_tier           # Lower → no AE/SDR handoff
            if not notify:
                # Lower tier → no handoff: never post (would drop an ownerless card into a
                # real channel). UI + auto-route never activate Lower; this guards direct
                # API calls. Release the claim so nothing is left stuck "activated".
                if claimed:
                    repo.release_activation(account_id)
                return {"posted": False, "skipped": "lower_tier", "account_id": account_id}
            # Routing (per the AE/SDR spec):
            #   Hot        → AE,  full packet (enriched decision-makers + intel brief)
            #   Warm/Some  → SDR, the SAME full packet (same process + information)
            #   Lower      → no handoff
            # Enrich (paid Apollo) for ANY notified tier so the SDR card carries the same
            # decision-makers as the AE card. A {"test": true} post never spends credits.
            dms: list[dict] = []
            if notify and account.get("domain") and not is_test:
                from auto_search.engagement import enrichment
                try:
                    dms = await enrichment.enrich_account(account["domain"],
                                                          company=account.get("name"))
                except Exception:  # noqa: BLE001 — never block the activation post
                    logger.exception("activation enrichment failed for %s", account_id)
            # Deep-link the "Open in console" button straight to THIS account's drawer
            # (the SPA reads ?view=engagement&account=… on load), not the generic home.
            app_url = os.getenv("ENGAGEMENT_APP_URL")
            if app_url:
                from urllib.parse import quote
                sep = "&" if "?" in app_url else "?"
                app_url = f"{app_url}{sep}view=engagement&account={quote(account_id)}"
            # Live routing: real @-pings + per-tier channel (Hot→AE, Warm/Some→SDR).
            # The console's runtime toggle (repo setting) overrides the env default;
            # OFF (default) or a {"test":true} post → plain-text names on the private
            # testing webhook, so testing never pings a real person or hits a channel
            # with people in it.
            override = repo.get_setting("live_routing")   # "1"/"0" from the UI, or None
            live = (override == "1") if override is not None else engagement_notify.live_routing()
            live = live and not is_test
            ids_override = None if live else {}   # None=use env IDs (ping); {}=plain @Name
            ae = engagement_notify.resolve_ae(account, ids=ids_override) if is_hot else None
            sdr_mention = (engagement_notify.resolve_sdr(account, ids=ids_override)
                           if is_sdr_tier else None)
            owner = ae or sdr_mention
            webhook = engagement_notify.tier_webhook(is_ae=is_hot) if live else None
            ok = await asyncio.to_thread(
                engagement_notify.activate_account, account, events,
                dms=dms, research=research, app_url=app_url, ae=owner, webhook=webhook,
                dm_limit=(2 if notify else 0), test=is_test)
            if not ok:
                raise HTTPException(status_code=502, detail="Slack post failed (check webhook)")
        except Exception:
            if claimed:   # release so the failed account can be retried, not stuck "activated"
                repo.release_activation(account_id)
            raise
        return {"posted": True, "account_id": account_id, "contacts": dms,
                "routed_to": owner, "reactivated": bool(force and not claimed)}

    @app.post("/api/engagement/activations/reset")
    async def engagement_activations_reset(request: Request):
        """Clear the activation ledger so accounts can be activated again — for the
        SDR/AE testing phase. Body {"account_id": "..."} resets ONE account; an empty
        body resets ALL. Returns how many were cleared."""
        repo = getattr(app.state, "engagement_repo", None)
        if not repo:
            raise HTTPException(status_code=503, detail="engagement store not available")
        body = await _json_body(request)
        account_id = (body.get("account_id") or "").strip()
        if account_id:
            repo.release_activation(account_id)
            return {"reset": 1, "account_id": account_id}
        return {"reset": repo.reset_activations()}

    def _live_routing_state(repo) -> dict:
        """Resolve the effective live-routing state: the console toggle (repo) wins
        over the env default. `source` tells the UI which one is in effect."""
        override = repo.get_setting("live_routing")
        if override is not None:
            return {"enabled": override == "1", "source": "override"}
        return {"enabled": engagement_notify.live_routing(), "source": "env"}

    @app.post("/api/engagement/notify-changes")
    def engagement_notify_changes(dry_run: bool = False, seed: bool = False, limit: int = 0):
        """Auto AE/SDR push. Posts a card when an account's tier ROSE above the last tier
        we notified it at (Some/Warm → SDR, Hot → AE) — OR when an already-Hot account gets
        NEW activity (Galyna 2026-07-05: a Hot account re-alerts on any new touch, old or
        new). Downward drift never re-sends. Respects the live-routing toggle (OFF → private
        test channel, plain names). Ledger = `notified_tiers` (account_id -> {tier, touch}).
          dry_run=true  → return what WOULD fire; no posts, no ledger change.
          seed=true     → baseline EVERY account to its CURRENT tier + latest touch WITHOUT
                          posting — the go-forward line. Nothing fires until a tier rise or
                          a NEW touch on a Hot account happens AFTER the seed. Run once when
                          turning the rule on (this is what stops the backlog flooding).
          limit=N       → cap posts to N (small test batch)."""
        import json
        from urllib.parse import quote
        repo = getattr(app.state, "engagement_repo", None)
        if not repo:
            raise HTTPException(status_code=503, detail="engagement store not available")
        ledger = json.loads(repo.get_setting("notified_tiers") or "{}")
        board = _engaged_view()
        if seed:
            # Baseline = the state as of NOW: each account's current tier AND its latest
            # touch are recorded as "already notified". So a tier rise OR a strictly-newer
            # touch on a Hot account (activity AFTER this seed) is the only thing that fires
            # — nothing back-fires. This is the "go forward from here" line.
            for a in board:
                ledger[a["account_id"]] = {"tier": a.get("tier") or "Lower",
                                           "touch": a.get("last_touch")}
            repo.set_setting("notified_tiers", json.dumps(ledger))
            return {"seeded": len(board), "format": "tier+touch"}
        due = engagement_notify.accounts_to_notify(board, ledger)
        live = _live_routing_state(repo)["enabled"]
        ids_override = None if live else {}   # None = env ids (ping); {} = plain @Name (test)
        app_base = os.getenv("ENGAGEMENT_APP_URL")   # deep-link back to the ABM console
        fired, posted = [], 0
        for d in due:
            a, tier, is_ae = d["account"], d["tier"], d["role"] == "ae"
            owner = (engagement_notify.resolve_ae(a, ids=ids_override) if is_ae
                     else engagement_notify.resolve_sdr(a, ids=ids_override))
            entry = {"account": a.get("name"), "from": d["prev"], "to": tier,
                     "reason": d.get("reason"),
                     "role": "AE" if is_ae else "SDR", "owner": owner,
                     "channel": (("AE" if is_ae else "SDR") + " channel") if live else "private-test"}
            if not dry_run and (not limit or posted < limit):
                webhook = engagement_notify.tier_webhook(is_ae=is_ae) if live else None
                # "Open in console" deep-link → this account's drawer in the ABM platform
                app_url = (f"{app_base}{'&' if '?' in app_base else '?'}"
                           f"view=engagement&account={quote(a['account_id'])}") if app_base else None
                events = repo.events_for_account(a["account_id"])
                ok = engagement_notify.activate_account(a, events, ae=owner, app_url=app_url,
                                                        webhook=webhook, dm_limit=0)
                entry["posted"] = bool(ok)
                if ok:
                    # Record BOTH tier and the touch we just notified on, so the same
                    # activity can't re-fire but a genuinely newer touch (Hot) can.
                    ledger[a["account_id"]] = {"tier": tier, "touch": d.get("touch")}
                    posted += 1
            fired.append(entry)
        if not dry_run:
            repo.set_setting("notified_tiers", json.dumps(ledger))
        return {"due": len(due), "posted": posted, "live": live,
                "dry_run": dry_run, "detail": fired[:60]}

    @app.get("/api/ops/changelog")
    def ops_changelog_list(limit: int = 100):
        """The automation change log — one centralized place for every change to
        automation logic / campaign rules / integrations / cadence (MAR2).
        Newest first."""
        import json
        repo = getattr(app.state, "engagement_repo", None)
        if not repo:
            raise HTTPException(status_code=503, detail="store not available")
        entries = json.loads(repo.get_setting("automation_changelog") or "[]")
        return {"entries": entries[-max(1, limit):][::-1], "total": len(entries)}

    @app.post("/api/ops/changelog")
    async def ops_changelog_add(request: Request):
        """Record an automation change AND push it to the changelog Slack channel.
        Body: {what (required), why, area, who, status, summary, change_id?}.
        Post one entry with status=initiated when starting a change, then another
        with the SAME change_id and status=completed (or rolled_back) when done —
        Slack gets a card each time (the ticket's two notifications)."""
        import json

        from auto_search.ops import changelog as changelog_mod
        repo = getattr(app.state, "engagement_repo", None)
        if not repo:
            raise HTTPException(status_code=503, detail="store not available")
        body = await _json_body(request)
        try:
            entry = changelog_mod.ChangeEntry(**{
                k: v for k, v in body.items()
                if k in ("change_id", "what", "why", "area", "who", "status", "summary")
                and v is not None})
        except Exception as e:  # noqa: BLE001 — pydantic validation → 422
            raise HTTPException(status_code=422, detail=str(e)) from None
        entries = json.loads(repo.get_setting("automation_changelog") or "[]")
        entries.append(entry.model_dump())
        repo.set_setting("automation_changelog", json.dumps(entries))
        posted = changelog_mod.post_change(entry)
        return {"entry": entry.model_dump(), "slack_posted": posted,
                "total": len(entries)}

    @app.post("/api/engagement/classify")
    async def engagement_classify(dry_run: bool = True, limit: int = 60):
        """Claude-classify engaged accounts that have NO scored framework, from name+domain.
        Stores ONLY high-confidence routable buckets (health_system/payer/specialty) into
        the ai_classifications setting; uncertain / non-ICP are left Unclassified so a guess
        never mislabels an account. dry_run=true (default) returns proposals without storing."""
        import asyncio
        import json

        from auto_search.engagement import classify as classify_mod
        repo = getattr(app.state, "engagement_repo", None)
        if not repo:
            raise HTTPException(status_code=503, detail="engagement store not available")
        existing = json.loads(repo.get_setting("ai_classifications") or "{}")
        todo = [a for a in _engaged_view()
                if not (a.get("framework") or a.get("segment"))
                and (a.get("score") or 0) > 0 and a["account_id"] not in existing][:limit]
        sem = asyncio.Semaphore(5)

        async def _one(a):
            async with sem:
                return a, await classify_mod.classify_account(a.get("name"), a.get("domain"))

        results = await asyncio.gather(*[_one(a) for a in todo])
        routable = ("health_system", "payer", "specialty")
        detail, applied = [], 0
        for a, r in results:
            row = {"account": a.get("name"), "framework": r["framework"],
                   "confidence": r["confidence"], "reason": r["reason"]}
            if not dry_run and r["confidence"] == "high" and r["framework"] in routable:
                existing[a["account_id"]] = r["framework"]
                applied += 1
                row["applied"] = True
            detail.append(row)
        if not dry_run:
            repo.set_setting("ai_classifications", json.dumps(existing))
        return {"evaluated": len(todo), "applied": applied, "stored_total": len(existing),
                "dry_run": dry_run, "detail": detail[:80]}

    @app.get("/api/engagement/settings/live-routing")
    async def engagement_live_routing_get():
        """Whether activation cards go to the real AE/SDR channels with @-pings (live)
        or stay on the private testing webhook with plain names (off)."""
        repo = getattr(app.state, "engagement_repo", None)
        if not repo:
            raise HTTPException(status_code=503, detail="engagement store not available")
        return _live_routing_state(repo)

    @app.post("/api/engagement/settings/live-routing")
    async def engagement_live_routing_set(request: Request):
        """Flip the live-routing toggle. Body {"enabled": true|false}. Persists in the
        repo so it survives restarts and is the source of truth over the env default.
        Toggling sends nothing — it only changes where the NEXT activation routes."""
        repo = getattr(app.state, "engagement_repo", None)
        if not repo:
            raise HTTPException(status_code=503, detail="engagement store not available")
        body = await _json_body(request)
        enabled = bool(body.get("enabled"))
        repo.set_setting("live_routing", "1" if enabled else "0")
        logger.info("engagement live-routing set to %s", "ON" if enabled else "OFF")
        return _live_routing_state(repo)

    @app.get("/api/engagement/settings/send-cutoff")
    async def engagement_send_cutoff_get():
        """The send-cutoff date (YYYY-MM-DD) or null. Activations only fire for accounts
        with activity on/after it; older 'already-processed' accounts are suppressed."""
        repo = getattr(app.state, "engagement_repo", None)
        if not repo:
            raise HTTPException(status_code=503, detail="engagement store not available")
        return {"cutoff": (repo.get_setting("activation_cutoff") or None)}

    @app.post("/api/engagement/settings/send-cutoff")
    async def engagement_send_cutoff_set(request: Request):
        """Set (or clear) the send-cutoff. Body {"cutoff": "YYYY-MM-DD"} sets it; an empty
        string clears it (everything becomes sendable). Changing it sends nothing."""
        repo = getattr(app.state, "engagement_repo", None)
        if not repo:
            raise HTTPException(status_code=503, detail="engagement store not available")
        body = await _json_body(request)
        raw = (body.get("cutoff") or "").strip()
        if raw and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
            raise HTTPException(status_code=400, detail="cutoff must be YYYY-MM-DD or empty")
        repo.set_setting("activation_cutoff", raw)   # empty clears the cutoff
        logger.info("engagement send-cutoff set to %s", raw or "(none)")
        return {"cutoff": raw or None}

    async def _linkedin_tofu(*, dry_run: bool, max_contacts: int | None = None,
                             max_leads: int | None = None, max_reactions: int = 50) -> dict:
        """Load the share CSV, build the sinks (Airtable + Reply.io, live runs only),
        and run the LinkedIn TOFU ad-engagement flow. Raises on misconfig — the caller
        decides whether that's fatal (standalone endpoint) or a skipped leg (full sync)."""
        from auto_search.engagement import linkedin_ads, linkedin_ads_runner
        csv_path = os.getenv("LINKEDIN_TOFU_CSV") or os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "engagement", "linkedin_tofu_shares.csv")
        with open(csv_path) as f:
            share_categories = linkedin_ads.load_share_categories(f.read())
        if not share_categories:
            raise RuntimeError("no usable share_ids in the LinkedIn TOFU CSV")
        airtable = reply = None
        if not dry_run:
            from auto_search.engagement.airtable_client import AirtableClient
            from auto_search.engagement.replyio_client import ReplyioClient
            airtable = AirtableClient()                 # the sink (upsert on Email)
            reply = ReplyioClient()
        return await linkedin_ads_runner.run(
            share_categories=share_categories, engagement_repo=app.state.engagement_repo,
            scoring_repo=app.state.scoring_repo, discovery_repo=app.state.repo,
            airtable_client=airtable, replyio_client=reply, max_reactions=max_reactions,
            max_contacts=max_contacts, max_leads=max_leads, dry_run=dry_run)

    async def _sync_all_sources(*, since: str, max_contacts: int | None) -> None:
        """Pull EVERY engagement source in one pass, best-effort. A failing leg is
        logged and skipped so the rest still run (mirrors the daily cron's all-legs
        policy); each leg is idempotent. Order: Reply.io → SFDC → podcast → LinkedIn."""
        repo, scoring, discovery = (app.state.engagement_repo, app.state.scoring_repo,
                                    app.state.repo)
        ok: list[str] = []
        try:    # 1. Reply.io email activity
            await engagement_sync_mod.run_sync(
                engagement_repo=repo, scoring_repo=scoring, discovery_repo=discovery,
                since=since, max_contacts=max_contacts)
            ok.append("replyio")
        except Exception:  # noqa: BLE001 — one source must not sink the others
            logger.exception("full sync leg failed: replyio")
        try:    # 2. Salesforce leads + booked meetings (blocking client → thread)
            await asyncio.to_thread(
                engagement_sync_mod.run_sfdc_sync, engagement_repo=repo,
                scoring_repo=scoring, discovery_repo=discovery, since=since)
            ok.append("sfdc")
        except Exception:  # noqa: BLE001
            logger.exception("full sync leg failed: sfdc")
        podcast_url = os.getenv("PODCAST_CSV_URL")
        if podcast_url:    # 3. Podcast leads (no-op without the URL)
            try:
                await asyncio.to_thread(
                    engagement_sync_mod.run_podcast_url_sync, engagement_repo=repo,
                    scoring_repo=scoring, discovery_repo=discovery, url=podcast_url)
                ok.append("podcast")
            except Exception:  # noqa: BLE001
                logger.exception("full sync leg failed: podcast")
        try:    # 4. LinkedIn TOFU ad reactions → Airtable + Reply.io + heat (live write)
            # Honor the caller's max_contacts so a manual "Sync all?max_contacts=N" caps
            # the paid Apollo-enrich/write here too (not just the Reply.io leg); steady
            # state is already bounded by the runner's already-processed dedup.
            await _linkedin_tofu(dry_run=False, max_contacts=max_contacts)
            ok.append("linkedin_tofu")
        except Exception:  # noqa: BLE001 — e.g. Airtable not configured; skip, don't fail
            logger.exception("full sync leg failed: linkedin_tofu")
        logger.info("engagement full sync done — legs ok: %s", ok)

    @app.post("/api/engagement/sync")
    def engagement_sync(since: str = "2026-01-01", max_contacts: int | None = None):
        """Sync ALL engagement sources in one pass: Reply.io email, Salesforce
        leads/meetings, podcast leads, and LinkedIn TOFU ad reactions. Best-effort
        (a failing source is logged and skipped, the rest still run), idempotent, one
        sync at a time. Returns immediately; the pull runs in the background."""
        repo = getattr(app.state, "engagement_repo", None)
        if not repo:
            raise HTTPException(status_code=503, detail="engagement store not available")
        if getattr(app.state, "engagement_running", False):
            return {"started": False, "busy": True}
        app.state.engagement_running = True

        async def _run() -> None:
            try:
                await _sync_all_sources(since=since, max_contacts=max_contacts)
            finally:
                app.state.engagement_running = False

        _schedule_coro(app, _run())
        return {"started": True}

    @app.post("/api/engagement/sfdc/sync")
    def engagement_sfdc_sync(since: str = "2026-01-01"):
        """Pull Salesforce (read-only): high-intent inbound leads created on/after
        `since` (YYYY-MM-DD), cross to scored/ABM accounts, score into heat. Only
        matched leads are stored. Shares the one-at-a-time lock."""
        repo = getattr(app.state, "engagement_repo", None)
        if not repo:
            raise HTTPException(status_code=503, detail="engagement store not available")
        if getattr(app.state, "engagement_running", False):
            return {"started": False, "busy": True}
        app.state.engagement_running = True

        async def _run() -> None:
            import asyncio
            try:
                # run_sfdc_sync is blocking (httpx.Client) — off the event loop.
                await asyncio.to_thread(
                    engagement_sync_mod.run_sfdc_sync,
                    engagement_repo=repo, scoring_repo=app.state.scoring_repo,
                    discovery_repo=app.state.repo, since=since)
            except Exception:  # noqa: BLE001 — never crash the loop
                logger.exception("sfdc engagement sync failed")
            finally:
                app.state.engagement_running = False

        _schedule_coro(app, _run())
        return {"started": True}

    @app.post("/api/engagement/linkedin/run")
    async def engagement_linkedin_run(dry_run: bool = True, max_contacts: int | None = None,
                                      max_leads: int | None = None, max_reactions: int = 50):
        """LinkedIn TOFU ad-engagement, standalone (the main /sync also runs this leg).
        Scrape post reactions -> drop staff -> ABM-only -> Apollo work email -> dedup.
        dry_run=false also WRITES: Airtable upsert + Reply.io contact + `linkedin_tofu`
        heat. Awaits and returns the funnel. Shares the one-at-a-time engagement lock."""
        repo = getattr(app.state, "engagement_repo", None)
        if not repo:
            raise HTTPException(status_code=503, detail="engagement store not available")
        if getattr(app.state, "engagement_running", False):
            return {"started": False, "busy": True}
        app.state.engagement_running = True
        try:
            return await _linkedin_tofu(dry_run=dry_run, max_contacts=max_contacts,
                                        max_leads=max_leads, max_reactions=max_reactions)
        except (OSError, RuntimeError) as e:
            raise HTTPException(status_code=500, detail=f"LinkedIn TOFU run failed: {e}") from e
        finally:
            app.state.engagement_running = False

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
        if len(data) > _MAX_XLSX_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"workbook too large (limit {_MAX_XLSX_BYTES // 1_000_000} MB)")
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
                        abm_lookup=_abm_social_lookup,
                        gate=ctrl.gate, posted_limit_date=since, max_enrich=cap)
                if do_events and not ctrl.cancelled:
                    app.state.run_phase = f"Scanning event keywords ({window})"
                    app.state.last_events = await poll_events(
                        keywords, repo=app.state.repo, op=op, can_qualify=budget_gate,
                        abm_lookup=_abm_social_lookup,
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
                "last_social": getattr(app.state, "last_social", None),
                "last_events": getattr(app.state, "last_events", None)}

    @app.get("/api/discovery/parked")
    def get_parked():
        """Jobs stacking watch list: companies with a single open STANDARD RCM
        role — parked (not yet qualified to save cost), re-checked every run,
        and auto-qualified once a second role opens. Drives the subtle
        "watching N" strip. Defensive: a repo without the ledger → empty watch.
        """
        repo = app.state.repo
        companies = repo.parked_companies() if hasattr(repo, "parked_companies") else []
        return {
            "companies": companies,
            "count": len(companies),
            "stack_min": job_stacking.STACK_MIN_STANDARD,
            "window_days": discovery_runner.JOBS_WINDOW_DAYS,
        }

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
                            abm_lookup=_abm_social_lookup,
                            gate=ctrl.gate, posted_limit_date=since, max_enrich=cap)
                    if keywords and not blocked and not ctrl.cancelled:
                        app.state.run_phase = "Scanning event keywords"
                        app.state.last_events = await poll_events(
                            keywords, repo=app.state.repo, op=op, can_qualify=s_gate,
                            abm_lookup=_abm_social_lookup,
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

    @app.post("/api/account/{account_id}/warm-intros")
    def find_warm_intros(account_id: str):
        """Find ICP decision-makers at this scored account, ranked by warmth
        against the founders' networks (engaged with Magical's posts > shared
        employer > shared school; evidence on every path).

        On demand (~$0.10-0.25/run: a people search + a one-time founder
        scrape), one at a time per account. The UI polls GET /api/account/{id}
        until warm_intros.state flips to 'ready'."""
        account = app.state.scoring.get(account_id)
        if account is None:
            raise HTTPException(status_code=404, detail="account not found")
        if account.get("state") != "scored":
            raise HTTPException(status_code=409, detail="account must be scored first")
        if (account.get("warm_intros") or {}).get("state") == "generating":
            return account                            # already in flight
        _assert_budget(app, 0.25)
        app.state.scoring_repo.set_warm_intros(account_id, {"state": "generating"})

        async def _run() -> None:
            op = spend_guard.Operation(app.state.scoring_repo, "warm_intros",
                                       estimated_usd=0.25, accounts_planned=1)

            def on_cost(usd: float, step: str) -> None:
                op.record(step=step, actual_usd=usd, account_id=account_id,
                          model="apify")

            try:
                # On-demand: the rep chose this account, so fully scrape its ICP
                # decision-makers via Apify for the richest cross-match (spend-guarded).
                payload = await intros_service.generate(
                    account, discovery_repo=app.state.repo, on_cost=on_cost,
                    scrape_contacts=True)
                app.state.scoring_repo.set_warm_intros(account_id, payload)
            except Exception as e:  # noqa: BLE001 — land in 'error', never crash the loop
                logger.exception("warm intros failed for %s", account_id)
                app.state.scoring_repo.set_warm_intros(account_id, {
                    "state": "error", "error": f"{type(e).__name__}: {e}"})
            finally:
                op.finish()

        _schedule_coro(app, _run())
        return app.state.scoring.get(account_id)

    @app.post("/api/scoring/warm-intros/run-all")
    def run_all_warm_intros(force: bool = False):
        """Backfill warm intros across every scored account that lacks them.

        Apollo (free) finds the decision-makers for ALL accounts; green/yellow
        (high/medium fit) additionally get freshdata school enrichment, so a
        shared alma mater — the widest warm net — can surface. Idempotent: an
        account already 'ready' or in flight is skipped unless force=true. Returns
        at once; the board polls each account's warm_intros.state to 'ready'."""
        rows = app.state.scoring.list_scored()

        def _needs(r: dict) -> bool:
            wi = r.get("warm_intros") or {}
            if wi.get("state") == "generating":
                return False                      # already in flight
            if force or wi.get("state") != "ready":
                return True                       # forced, or no intros yet
            # Already has intros: re-run only green/yellow whose intros predate the
            # full Apify contact scrape, so the employer+school net is backfilled
            # exactly once. Red/low keep their free Apollo list untouched (no re-pay).
            return (r.get("tier_band") in ("high", "medium")
                    and not wi.get("contacts_scraped"))

        todo = [r["account_id"] for r in rows if _needs(r)]
        if not todo:
            return {"scheduled": 0, "skipped": len(rows)}
        # Apollo is free; only the green/yellow school enrichment costs. Size the
        # estimate at the per-contact cap so the monthly guard can refuse a run
        # it can't afford (429) before any money is spent.
        todo_set = set(todo)
        green_yellow = sum(1 for r in rows if r["account_id"] in todo_set
                           and r.get("tier_band") in ("high", "medium"))
        est = spend_guard.estimate_batch(
            green_yellow * intros_profiles.max_contacts(),
            intros_profiles.ENRICH_CONTACT_COST_USD)
        _assert_budget(app, est)
        for aid in todo:
            app.state.scoring_repo.set_warm_intros(aid, {"state": "generating"})

        async def _run_all() -> None:
            op = spend_guard.Operation(app.state.scoring_repo, "warm_intros_batch",
                                       estimated_usd=est, accounts_planned=len(todo))
            limit = max(1, int(os.getenv("INTROS_BATCH_CONCURRENCY", "6")))
            sem = asyncio.Semaphore(limit)

            async def _one(aid: str) -> None:
                account = app.state.scoring.get(aid)
                if not account:
                    return
                # Stop paying once the op overheats: everyone still gets the free
                # Apollo contact list, the paid school net just switches off.
                enrich = (account.get("tier_band") in ("high", "medium")
                          and not op.overheated())

                def on_cost(usd: float, step: str) -> None:
                    op.record(step=step, actual_usd=usd, account_id=aid, model="apify")

                async with sem:
                    try:
                        payload = await intros_service.generate(
                            account, discovery_repo=app.state.repo,
                            on_cost=on_cost, scrape_contacts=enrich)
                        app.state.scoring_repo.set_warm_intros(aid, payload)
                    except Exception as e:  # noqa: BLE001 — one account mustn't kill the batch
                        logger.exception("batch warm intros failed for %s", aid)
                        app.state.scoring_repo.set_warm_intros(aid, {
                            "state": "error", "error": f"{type(e).__name__}: {e}"})
                    finally:
                        op.accounts_done += 1

            try:
                await asyncio.gather(*(_one(a) for a in todo))
            finally:
                op.finish()

        _schedule_coro(app, _run_all())
        return {"scheduled": len(todo), "enrich_green_yellow": green_yellow,
                "estimated_usd": est}

    @app.post("/api/scoring/import/preview")
    async def import_preview(request: Request):
        """Parse a CSV (raw request body) and report the schema + column mapping
        + dedupe, without persisting — the wizard's review step."""
        result = _parse_upload(await request.body())
        return _preview_payload(app, result)

    @app.post("/api/scoring/import")
    async def import_commit(request: Request):
        """Parse the CSV body and enqueue accounts as 'queued' — parked, NOT scored.
        Scoring is on demand (per-account or a batch) so importing a large file
        never spends money by itself.

        Dedup is by company IDENTITY: a row already scored is skipped, and a row
        that's a LIVE discovery company is MOVED into Scored carrying its signals
        (it leaves the Discovery panel) instead of creating a signal-less twin.
        Each batch is tagged with a label so it can be filtered + exported later."""
        result = _parse_upload(await request.body())
        label = _import_label(request.headers.get("x-import-filename"))
        svc_ = svc(app)
        scoring = app.state.scoring
        csv_fresh, moved, skipped = [], [], 0
        for a in result.accounts:
            action, company = _classify_import_row(
                a.name, a.account_id, get_company=svc_.get_company, exists=scoring.exists)
            if action == "skip":
                skipped += 1
            elif action == "move":
                svc_.promote(company.company_key)          # leaves the Discovery panel
                moved.append(scoring.enqueue_discovery(company.model_dump(), state="queued"))
            else:
                csv_fresh.append(a)
        csv_rows = scoring.enqueue_csv(csv_fresh, state="queued", import_label=label)
        return {
            "schema_label": result.schema_label,
            "segment": result.segment,
            "imported": len(csv_rows) + len(moved),
            "queued": len(csv_rows) + len(moved),
            "moved_from_discovery": len(moved),
            "skipped_known": skipped,
            "import_label": label,
            "accounts": csv_rows + moved,
        }

    @app.get("/api/scoring/imports")
    def scoring_imports():
        """The distinct CSV import batches (label + count), newest first — feeds
        the Import filter so a user can isolate and export their own upload."""
        return {"imports": app.state.scoring_repo.import_labels()}

    @app.post("/api/scoring/lookup")
    async def scoring_lookup(request: Request):
        """AE one-off lookup, step 1 — resolve WHO the typed company is before
        any deep-score spend. Free when we already have it (scored account or a
        live Discovery company); otherwise one Exa search + one no-tools identity
        pass (~$0.01, recorded as a spend op). Confidence is gated in code:
        ambiguous/low/non-ICP outcomes come back as such — the AE always confirms
        via the card; nothing auto-scores from here."""
        body = await _json_body(request)
        name = str(body.get("name") or "").strip()
        if not name:
            raise HTTPException(status_code=422, detail="company name is required")
        website = str(body.get("website") or "").strip() or None
        out = await ae_lookup.resolve(
            name, website,
            accounts=app.state.scoring.list_scored(),
            get_company=svc(app).get_company)
        cost = float(out.get("cost_usd") or 0)
        if cost:   # paid path ran (Exa + identity) — audit it like every paid step
            op = spend_guard.Operation(app.state.scoring_repo, "ae_lookup_resolve",
                                       estimated_usd=cost, accounts_planned=1,
                                       metadata={"name": name})
            op.record(step="resolve", actual_usd=cost, company_key=normalize_company_name(name))
            op.finish()
        try:
            eng_rows = _engaged_view()   # shaped board rows (names/tiers resolved)
        except Exception:  # noqa: BLE001 — garnish only, lookup never depends on it
            logger.exception("lookup engagement context failed")
            eng_rows = []
        ctx = _lookup_engagement_context(eng_rows, name, website)
        if ctx:
            # Tell the AE their engagement history will inform the score.
            ctx["signals"] = len(_engagement_intent_signals(
                getattr(app.state, "engagement_repo", None), name,
                ctx.get("account_id")))
        out["engagement"] = ctx
        return out

    @app.post("/api/scoring/lookup/score")
    async def scoring_lookup_score(request: Request):
        """AE one-off lookup, step 2 — the AE confirmed the resolve card. Create
        the account (source='ae') and run the NORMAL scoring pipeline on it:
        same engine, same rubric, same independent QA, same spend caps — a
        one-off score can never diverge from a batch score.

        Budget-aware like promote: no headroom -> the account parks as 'queued'
        (nothing spends) and the response says so. Race-safe: identity is
        re-checked at commit; an existing queued/error row is re-kicked rather
        than duplicated (a csv_/acc_ id mismatch can never create a twin)."""
        body = await _json_body(request)
        try:
            account = ae_lookup.build_account(
                name=str(body.get("name") or ""),
                domain=str(body.get("domain") or ""),
                segment=str(body.get("segment") or ""),
                sub_segment=(str(body.get("sub_segment")).strip()
                             if body.get("sub_segment") else None),
                description=str(body.get("description") or "").strip(),
                hq=(str(body.get("hq")).strip() if body.get("hq") else None),
                evidence_url=(str(body.get("evidence_url")).strip()
                              if body.get("evidence_url") else None),
                approximate_employees=body.get("approximate_employees"))
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e)) from None

        kind, hit = ae_lookup.find_existing(
            account.name, account.domain,
            accounts=app.state.scoring.list_scored(),
            get_company=svc(app).get_company)
        if kind == "discovery":
            # Belongs to the promote path (carries its live signals) — the UI
            # calls POST /api/company/{key}/promote instead.
            return {"status": "in_discovery",
                    "company_key": getattr(hit, "company_key", None)}

        summary = app.state.scoring_repo.cost_summary()
        affordable = budget_guard.remaining(summary) >= budget_guard.EST_SCORE_COST
        if kind == "scored":
            # Use the EXISTING row's id (may be csv_*), never the freshly built
            # one — that id mismatch is exactly how twins would be born.
            existing_id = hit.get("account_id")
            if hit.get("state") in ("queued", "error"):
                if affordable:
                    _schedule_scoring(app, existing_id, op_type="ae_lookup")
                    return {"status": "scoring", "account_id": existing_id,
                            "account": app.state.scoring.get(existing_id),
                            "rekicked": True, "auto_brief": _ae_brief_auto()}
                # Parked/failed row + no budget headroom: say so honestly —
                # "already_scored" here would hide that the click did nothing.
                return {"status": "queued", "account_id": existing_id,
                        "account": hit, "budget_blocked": True}
            return {"status": "already_scored", "account_id": existing_id,
                    "account": hit}

        # Persist the account's first-party engagement intent as carried signals
        # so the drawer shows WHY intent scored (the score-time merge in
        # ScoringService covers re-scores; this covers provenance).
        account.discovery_signals = _engagement_intent_signals(
            getattr(app.state, "engagement_repo", None), account.name)
        # CMS MSSP ACO participation (providers only): a strong value-based-care
        # signal + leadership names, injected as KNOWN FACTS for the score +
        # brief. Best-effort, cached daily, never blocks the lookup.
        if account.segment in ("health_system", "specialty"):
            try:
                account.firmographics.update(
                    await asyncio.to_thread(cms_aco.aco_known_facts, account.name))
            except Exception:  # noqa: BLE001 — enrichment garnish
                logger.exception("CMS ACO enrichment failed for %s", account.name)
        row = app.state.scoring.enqueue_account(
            account, state="scoring" if affordable else "queued")
        if affordable:
            _schedule_scoring(app, row["account_id"], op_type="ae_lookup")
        return {"status": "scoring" if affordable else "queued",
                "account_id": row["account_id"], "account": row,
                "budget_blocked": not affordable,
                "auto_brief": affordable and _ae_brief_auto()}

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
