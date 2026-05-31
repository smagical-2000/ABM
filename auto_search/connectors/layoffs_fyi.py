"""Layoffs.fyi connector.

Source: https://layoffs.fyi/
Public data backed by Airtable. The most reliable access path right now
is to download the CSV manually from the site (Download button on the
homepage) and point this connector at the local file. When we move to
production we'll switch to scraping the Airtable embed on a cron.

The connector applies cheap pre-filters at extract-time (industry, geo,
scale) so we don't ship noise into the rest of the pipeline. The full ICP
qualification still runs downstream in qualifier.py — but pre-filtering
here cuts the LLM call volume by ~10x.

Expected CSV columns (as of May 2026):
    Company, Location HQ, # Laid Off, Date, %, Industry, Source,
    Stage, Funds Raised, Country, Date Added
"""

from __future__ import annotations

import csv
import io
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterator

import httpx

from auto_search.connectors.base import SignalConnector
from auto_search.models import RawSignal


# Healthcare-adjacent industry keywords (broad — strict filter happens later)
HEALTHCARE_KEYWORDS = (
    "health", "medical", "hospital", "clinic", "rcm", "revenue cycle",
    "insurance", "payer", "behavioral", "mental", "biotech", "pharma",
    "care", "therapy", "orthop", "surgery", "ambulatory",
)

# US-ish country values seen in the dataset
US_COUNTRY_VALUES = {"", "USA", "US", "UNITED STATES", "U.S.", "U.S.A."}


class LayoffsFyiConnector:
    source_name = "layoffs_fyi"
    signal_types = ["layoff"]
    default_cron = "0 6 * * *"   # 6am UTC daily

    def __init__(
        self,
        csv_path: str | None = None,
        csv_url: str | None = None,
    ) -> None:
        """Either pass a local CSV path or a remote URL. CLI also accepts
        these via env vars (LAYOFFS_CSV_PATH / LAYOFFS_CSV_URL).
        """
        self.csv_path = csv_path or os.getenv("LAYOFFS_CSV_PATH")
        self.csv_url = csv_url or os.getenv("LAYOFFS_CSV_URL")
        if not self.csv_path and not self.csv_url:
            raise ValueError(
                "Layoffs connector needs either csv_path or csv_url. "
                "Set LAYOFFS_CSV_PATH=./data/layoffs.csv in .env after "
                "downloading from https://layoffs.fyi/"
            )

    # ---- public ------------------------------------------------------

    async def pull(self, since: datetime) -> AsyncIterator[RawSignal]:
        text = await self._load_csv()
        reader = csv.DictReader(io.StringIO(text))

        for row in reader:
            signal = self._row_to_signal(row, since)
            if signal is not None:
                yield signal

    # ---- internals ---------------------------------------------------

    async def _load_csv(self) -> str:
        if self.csv_path:
            path = Path(self.csv_path).expanduser()
            if not path.exists():
                raise FileNotFoundError(
                    f"Layoffs CSV not found at {path}. "
                    "Download from https://layoffs.fyi/ → Download CSV."
                )
            return path.read_text(encoding="utf-8")

        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as c:
            resp = await c.get(self.csv_url)  # type: ignore[arg-type]
            resp.raise_for_status()
            return resp.text

    def _row_to_signal(
        self, row: dict[str, str], since: datetime
    ) -> RawSignal | None:
        # date — skip rows we can't parse
        observed_at = _parse_date(row.get("Date") or row.get("date") or "")
        if observed_at is None or observed_at < since:
            return None

        company = (row.get("Company") or row.get("company") or "").strip()
        if not company:
            return None

        # pre-filter 1: industry must be healthcare-adjacent
        industry = (row.get("Industry") or row.get("industry") or "").lower()
        if not any(k in industry for k in HEALTHCARE_KEYWORDS):
            return None

        # pre-filter 2: US-only
        country = (row.get("Country") or row.get("country") or "").upper().strip()
        if country and country not in US_COUNTRY_VALUES:
            return None

        # pre-filter 3: minimum scale (rule of thumb — confirm with Galyna)
        laid_off = _to_int(row.get("# Laid Off") or row.get("laid_off"))
        if laid_off is not None and laid_off < 10:
            return None

        # Compound external_id since layoffs.fyi has no stable ID
        external_id = f"{_slug(company)}::{observed_at.date().isoformat()}"

        return RawSignal(
            source=self.source_name,
            source_external_id=external_id,
            signal_type="layoff",
            company_name_raw=company,
            company_domain_raw=None,
            observed_at=observed_at,
            signal_strength=_compute_strength(industry, laid_off),
            payload={
                "laid_off_count": laid_off,
                "percent_laid_off": row.get("%") or row.get("percent_laid_off"),
                "industry_raw": row.get("Industry") or row.get("industry"),
                "location_hq": row.get("Location HQ") or row.get("location_hq"),
                "stage": row.get("Stage") or row.get("stage"),
                "funds_raised_musd": row.get("Funds Raised")
                    or row.get("funds_raised"),
                "source_url": row.get("Source") or row.get("source"),
                "country": row.get("Country") or row.get("country"),
            },
        )


# ---- pure helpers ---------------------------------------------------

def _parse_date(s: str) -> datetime | None:
    s = s.strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _to_int(s: str | None) -> int | None:
    if not s:
        return None
    try:
        return int(str(s).replace(",", "").strip())
    except (ValueError, AttributeError):
        return None


def _slug(s: str) -> str:
    return "".join(c.lower() if c.isalnum() else "_" for c in s).strip("_")


def _compute_strength(industry: str, laid_off: int | None) -> float:
    """Heuristic signal strength based on industry + scale.

    Higher = more likely to be a real ICP fit. The LLM still does the
    final qualification — this just helps the queue sort sensibly.
    """
    if "rcm" in industry or "revenue cycle" in industry:
        base = 0.90
    elif "hospital" in industry or "health system" in industry:
        base = 0.75
    elif "behavioral" in industry or "mental" in industry:
        base = 0.70
    elif "insurance" in industry or "payer" in industry:
        base = 0.70
    elif "health" in industry or "medical" in industry:
        base = 0.55
    else:
        base = 0.40

    if laid_off and laid_off >= 200:
        base = min(0.95, base + 0.10)
    elif laid_off and laid_off >= 50:
        base = min(0.92, base + 0.05)

    return round(base, 2)
