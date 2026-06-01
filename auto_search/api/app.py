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

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from auto_search.db import get_repository
from auto_search.services import DiscoveryStats, PanelCompany, ReviewService

load_dotenv(override=True)
logger = logging.getLogger(__name__)

_WEB_DIR = Path(__file__).resolve().parent.parent.parent / "web" / "discovery"


class RejectBody(BaseModel):
    reason: str


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Build the service once; the repo (Postgres pool or JSON file) lives for
    # the app's lifetime. Stored on app.state so handlers reuse it.
    repo = get_repository()
    app.state.service = ReviewService(repo)
    app.state.repo = repo
    logger.info("discovery API ready (repo=%s)", type(repo).__name__)
    try:
        yield
    finally:
        close = getattr(repo, "close", None)
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
        # status is the machine verdict tab: 'qualified' (default) or 'needs_review'.
        statuses = ("needs_review",) if status == "needs_review" else ("qualified",)
        return svc(app).list_panel(
            statuses=statuses, segment=segment, signal_type=signal_type
        )

    @app.get("/api/company/{key}", response_model=PanelCompany)
    def get_company(key: str):
        company = svc(app).get_company(key)
        if company is None:
            raise HTTPException(status_code=404, detail="company not found")
        return company

    # ── workflow ───────────────────────────────────────────────────────

    @app.post("/api/company/{key}/promote")
    def promote(key: str):
        try:
            account_id = svc(app).promote(key)
        except KeyError:
            raise HTTPException(status_code=404, detail="company not found") from None
        return {"account_id": account_id}

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

    @app.get("/api/health")
    def health():
        return {"ok": True}

    # ── static UI (mounted last so /api/* wins) ────────────────────────
    if _WEB_DIR.is_dir():
        app.mount("/", StaticFiles(directory=str(_WEB_DIR), html=True), name="ui")

    return app


app = create_app()
