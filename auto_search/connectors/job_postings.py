"""Job-postings connector — Indeed + LinkedIn, via Apify scrapers.

The single most on-the-nose buying signal for Magical: a US healthcare provider
posting revenue-cycle roles (coders, billers, AR/denials/prior-auth staff). That
manual RCM headcount is exactly what agentic automation replaces — "automate
these instead of hiring eight people."

How it works
------------
SignalBase's hiring feed has no title filter, so we search each board's quoted
title field for each ESSENTIAL RCM role, last-24h, a few rows each, across BOTH
Indeed and LinkedIn. Every posting becomes a `job_posting` signal about the
HIRING company. Running both boards lifts yield; the same company found on both
merges automatically downstream (dedup is on company_key), and the same role at
the same company/city is deduped across boards here.

Qualification — three layers, cheap → expensive
    1. ROLE keyword gate (here)     — free; drops obvious non-RCM titles.
    2. JOB qualifier (Sonnet)       — cheap; reads title + JD, confirms it's a
       hands-on RCM operations role (not an educator, RCM-software engineer,
       sales, etc.). Runs as a pipeline pre-filter — see job_qualifier.py.
    3. COMPANY/ICP qualifier (web)  — expensive; the existing website qualifier
       confirms the EMPLOYER is provider/payer ICP, not an RCM vendor/lab/staffing.
    Connectors stay LLM-free (pure + testable); layers 2–3 live in the pipeline.

Volume > seniority
------------------
Each posting is its own signal, tagged with a short `role` bucket (Coder,
Biller, Denials…). The pipeline groups by company, so a hospital posting eight
coder roles surfaces as one company with eight job_posting signals — the count,
grouped by role, is the pain-intensity readout the UI renders ("3 Coder jobs").

Cost: both actors bill per row. Keep `max_rows` small and search only the
essential titles below.
"""

from __future__ import annotations

import logging
import os
import re
from collections import Counter
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import NamedTuple

from auto_search.clients.apify_jobs import ApifyJobsClient, IndeedJob, LinkedInJob
from auto_search.models import RawSignal
from auto_search.normalize import clean_domain, parse_iso_datetime, slugify

logger = logging.getLogger(__name__)

# The essential RCM titles to search, each a (quoted query, role bucket,
# base signal strength, TIER). The role bucket is what the UI groups on
# ("3 Coder jobs"); quotes give an exact-phrase title match. Order high-value
# first — signal_strength also picks the representative posting for qualify.
#
# TIER shapes WHEN we spend the (expensive) company qualifier — see
# job_stacking.py. It does NOT change what's scraped; every posting is still
# pulled and stored.
#   • "core"     — the high-intent RCM work Magical automates directly (prior
#                  auth, denials/appeals, eligibility, claims, revenue
#                  cycle/integrity, utilization review). ONE posting is a buying
#                  signal on its own → qualify the company.
#   • "standard" — higher-volume / noisier adjacent roles (billers, coders,
#                  patient access, scheduling…). A lone posting is often routine
#                  backfill, so we only qualify once a company STACKS (≥2
#                  standard postings = a real revenue-cycle build-out). A
#                  single-standard company is parked and watched until it does,
#                  so we never pay to qualify noise nor lose a company that
#                  later stacks.
class EssentialTitle(NamedTuple):
    query: str          # quoted exact-phrase board query
    role: str           # UI grouping bucket
    strength: float     # base signal strength (0–1)
    tier: str           # CORE | STANDARD


CORE, STANDARD = "core", "standard"

ESSENTIAL_RCM_TITLES: list[EssentialTitle] = [
    # ── CORE (11) — a single posting qualifies the company ───────────────
    # The high-intent back-office RCM work Magical automates: front-end auth /
    # eligibility, claims, denials/appeals, revenue cycle/integrity, UM. One
    # open req at a provider is a buying signal on its own.
    EssentialTitle('"prior authorization specialist"', "Prior Auth", 0.88, CORE),
    EssentialTitle('"authorization coordinator"', "Prior Auth", 0.84, CORE),
    EssentialTitle('"insurance verification specialist"', "Eligibility", 0.85, CORE),
    EssentialTitle('"eligibility specialist"', "Eligibility", 0.82, CORE),
    EssentialTitle('"claims specialist"', "Claims", 0.82, CORE),
    EssentialTitle('"claims processor"', "Claims", 0.80, CORE),
    EssentialTitle('"denials specialist"', "Denials", 0.86, CORE),
    EssentialTitle('"appeals specialist"', "Appeals", 0.84, CORE),
    EssentialTitle('"revenue cycle specialist"', "Revenue Cycle", 0.82, CORE),
    EssentialTitle('"revenue integrity specialist"', "Revenue Integrity", 0.80, CORE),
    EssentialTitle('"utilization management nurse"', "Utilization Mgmt", 0.78, CORE),
    # ── STANDARD (13) — must STACK (≥2 postings) to spend the qualifier ──
    # Higher-volume / more clinical-adjacent roles. A lone posting is often
    # routine backfill (and titles like "billing/collections/scheduling/care
    # coordinator" are cross-industry), so we only qualify once a company
    # stacks — a single-standard company is parked & watched until it does.
    EssentialTitle('"medical biller"', "Biller", 0.72, STANDARD),
    EssentialTitle('"billing specialist"', "Biller", 0.66, STANDARD),
    EssentialTitle('"medical coder"', "Coder", 0.72, STANDARD),
    EssentialTitle('"cdi specialist"', "CDI", 0.70, STANDARD),
    EssentialTitle('"collections specialist"', "AR / Collections", 0.66, STANDARD),
    EssentialTitle('"payment posting specialist"', "Payment Posting", 0.66, STANDARD),
    EssentialTitle('"patient access representative"', "Patient Access", 0.68, STANDARD),
    EssentialTitle('"referral coordinator"', "Patient Access", 0.64, STANDARD),
    EssentialTitle('"intake coordinator"', "Patient Access", 0.62, STANDARD),
    EssentialTitle('"scheduling coordinator"', "Scheduling", 0.60, STANDARD),
    EssentialTitle('"care coordinator"', "Care Coordination", 0.58, STANDARD),
    EssentialTitle('"patient navigator"', "Care Coordination", 0.58, STANDARD),
    EssentialTitle('"clinical reviewer"', "Clinical Review", 0.60, STANDARD),
]


def select_titles(env_value: str | None = None) -> list[EssentialTitle]:
    """Pick which RCM titles to search, from DISCOVERY_JOBS_TITLES.

    The env var is a comma-separated allowlist matched (case-insensitively) against
    each title's role bucket OR its query text, so values like
    "revenue cycle, medical coder, biller" narrow the search to just those roles.
    This is the main jobs cost knob for controlled test runs — fewer titles means
    fewer Apify rows AND fewer downstream qualifications. Unset/empty → all titles.
    An allowlist that matches nothing falls back to all (never silently search 0).
    """
    raw = (env_value if env_value is not None
           else os.getenv("DISCOVERY_JOBS_TITLES", "")).strip()
    if not raw:
        return ESSENTIAL_RCM_TITLES
    wanted = [t.strip().lower() for t in raw.split(",") if t.strip()]
    selected = [
        t for t in ESSENTIAL_RCM_TITLES
        if any(w in t[1].lower() or w in t[0].lower() for w in wanted)
    ]
    if not selected:
        logger.warning("DISCOVERY_JOBS_TITLES=%r matched no titles — using all", raw)
        return ESSENTIAL_RCM_TITLES
    logger.info("jobs titles restricted to %s via DISCOVERY_JOBS_TITLES",
                [t[1] for t in selected])
    return selected

# RCM-keyword sanity gate: a returned title must contain one of these to count
# as an actual revenue-cycle role (drops the occasional off-topic match the
# boards return even for a quoted query). Broad on purpose — precision comes
# from the quoted search; this just removes obvious noise.
_RCM_KEYWORDS = (
    "coder", "coding", "biller", "billing", "revenue cycle", "revenue integrity",
    "accounts receivable", "a/r", "ar specialist", "ar follow",
    "denial", "appeal", "prior auth", "authorization", "pre-cert", "precert",
    "insurance verification", "eligibility", "claims", "charge entry",
    "charge capture", "patient access", "patient account", "reimbursement",
    "collections", "payment posting", "rcm",
    # roles added with the 24-title expansion — keep the gate in step so their
    # postings aren't dropped as "not_rcm_title" before the qualifier sees them
    "utilization", "clinical documentation", "cdi", "intake",
    "referral", "schedul", "clinical review", "care coordinat", "navigator",
)

# Job descriptions can be long; clip before we store/qualify them.
_JD_CLIP = 1500

DEFAULT_SOURCES = ("indeed", "linkedin")


class JobPostingsConnector:
    """Pull recent US healthcare RCM job postings from Indeed + LinkedIn."""

    source_name = "jobs"
    signal_types = ["job_posting"]
    default_cron = "0 10 * * *"  # 10:00 UTC daily

    def __init__(
        self,
        *,
        client: ApifyJobsClient | None = None,
        titles: list[EssentialTitle] | None = None,
        sources: tuple[str, ...] = DEFAULT_SOURCES,
        max_rows: int = 10,
        country: str = "us",
    ) -> None:
        self._client = client or ApifyJobsClient()
        self._titles = titles or select_titles()
        self._sources = sources
        self._max_rows = max_rows
        self._country = country

    async def pull(self, since: datetime) -> AsyncIterator[RawSignal]:
        """Yield a job_posting signal per US healthcare RCM opening on/after
        `since`, across the configured boards.

        Dedup: within a board by the board's job id; across boards by
        (company, title, city) so a posting cross-listed on both isn't counted
        twice — while distinct same-title reqs from one board are kept (volume).
        """
        from_days = _since_to_from_days(since)
        days = _since_to_days(since)
        drops: Counter[str] = Counter()
        seen_ids: set[str] = set()
        seen_comp: dict[tuple[str, str, str], str] = {}   # composite -> board
        yielded = 0

        for title in self._titles:
            boards = self._boards_for(title.tier)
            for board, job in await self._gather(title.query, from_days, days, boards):
                raw_id = _board_id(board, job)
                idkey = f"{board}:{raw_id}"          # internal cross-board dedup
                if idkey in seen_ids:
                    drops["dup_id"] += 1
                    continue

                # source_external_id stays the raw board id; the DB namespaces
                # signal dedup by (source, source_external_id) already.
                mapper = _job_to_signal if board == "indeed" else _linkedin_to_signal
                signal, reason = mapper(
                    job, title.role, title.strength, title.tier, since, raw_id)
                if signal is None:
                    drops[reason] += 1
                    continue

                comp = _composite_key(signal)
                prev_board = seen_comp.get(comp)
                if prev_board is not None and prev_board != board:
                    drops["dup_cross_board"] += 1   # same role/co/city on 2 boards
                    continue

                seen_ids.add(idkey)
                seen_comp.setdefault(comp, board)
                yielded += 1
                yield signal

        logger.info("jobs pull done — yielded=%d (sources=%s)", yielded, self._sources)
        for reason, n in drops.most_common():
            logger.info("  dropped %d  %s", n, reason)

    def _boards_for(self, tier: str) -> tuple[str, ...]:
        """Which boards to search for a title, by tier — the main scrape-cost
        lever. CORE titles use every configured board (max recall on the
        high-intent roles we most want to catch). STANDARD titles use Indeed
        ONLY — it's the higher-yield board, and these high-volume/noisier roles
        have to STACK to qualify anyway, so paying for a second board on each is
        wasteful. Falls back to the full set if Indeed isn't configured.
        """
        if tier == STANDARD and "indeed" in self._sources:
            return ("indeed",)
        return self._sources

    async def _gather(self, query: str, from_days: str, days: int,
                      sources: tuple[str, ...] | None = None):
        """Fetch one title from the given boards (default: all configured);
        tolerate a board failing or a client that doesn't implement it (keeps
        unit tests board-agnostic).
        """
        sources = sources or self._sources
        hits: list[tuple[str, object]] = []
        if "indeed" in sources and hasattr(self._client, "search_indeed"):
            try:
                rows = await self._client.search_indeed(
                    query, country=self._country,
                    from_days=from_days, max_rows=self._max_rows)
                hits += [("indeed", j) for j in rows]
            except Exception as e:  # noqa: BLE001 — one board mustn't kill the run
                logger.warning("indeed search %s failed: %s", query, e)
        if "linkedin" in sources and hasattr(self._client, "search_linkedin"):
            try:
                rows = await self._client.search_linkedin(
                    query.strip('"'), days=days, limit=self._max_rows)
                hits += [("linkedin", j) for j in rows]
            except Exception as e:  # noqa: BLE001
                logger.warning("linkedin search %s failed: %s", query, e)
        return hits


# ── record → signal (Indeed) ──────────────────────────────────────────


def _job_to_signal(
    job: IndeedJob, role: str, strength: float, tier: str, since: datetime, key: str
) -> tuple[RawSignal | None, str]:
    """Map one Indeed posting to a signal about the hiring company, or drop it."""
    company = (job.companyName or "").strip()
    if not company:
        return None, "missing_company"

    if not _looks_rcm(job.title):
        return None, "not_rcm_title"

    observed_at = parse_iso_datetime(job.datePublished)
    if observed_at is None:
        return None, "unparseable_date"
    if observed_at.date() < since.date():   # date-granular: Indeed has no clock
        return None, "before_window"

    loc = job.location if isinstance(job.location, dict) else {}
    cc = (loc.get("countryCode") or "").upper()
    if cc and cc != "US":
        return None, "non_us"

    return (
        RawSignal(
            source="indeed",
            source_external_id=key,
            signal_type="job_posting",
            company_name_raw=company,
            company_domain_raw=_indeed_domain(job),
            observed_at=observed_at,
            signal_strength=strength,
            payload={
                "role": role,                       # ← UI grouping bucket
                "tier": tier,                       # ← core | standard (stacking gate)
                "job_title": job.title,
                "job_url": job.jobUrl,
                "apply_url": job.applyUrl,
                "location": loc.get("formattedAddressShort") or loc.get("city"),
                "city": loc.get("city"),
                "state": _state(loc),
                "country": loc.get("countryCode"),
                "date_published": job.datePublished,
                "age": job.age,
                "job_type": job.jobType,
                "is_remote": _truthy(job.isRemote),
                "salary": _salary_text(job.salary),
                "description": _clip(job.descriptionText),
                "company_website": _indeed_domain(job),
                "company_indeed_url": job.companyUrl,
                "source_board": "indeed",
            },
        ),
        "",
    )


# ── record → signal (LinkedIn) ────────────────────────────────────────


def _linkedin_to_signal(
    job: LinkedInJob, role: str, strength: float, tier: str, since: datetime, key: str
) -> tuple[RawSignal | None, str]:
    """Map one LinkedIn posting to a signal about the hiring company, or drop it.

    LinkedIn gives no corporate website (companyUrl is a linkedin.com page), so
    the domain is left to the company qualifier to resolve. Location is a free
    string like 'Dallas, TX'; we parse city/state best-effort. US scope comes
    from the search input (location='United States').
    """
    company = (job.companyName or "").strip()
    if not company:
        return None, "missing_company"

    if not _looks_rcm(job.title):
        return None, "not_rcm_title"

    observed_at = parse_iso_datetime(job.postedDate)
    if observed_at is None:
        return None, "unparseable_date"
    if observed_at.date() < since.date():
        return None, "before_window"

    loc = (job.location or "").strip()
    city, state = _split_loc(loc)
    return (
        RawSignal(
            source="linkedin",
            source_external_id=key,
            signal_type="job_posting",
            company_name_raw=company,
            company_domain_raw=None,
            observed_at=observed_at,
            signal_strength=strength,
            payload={
                "role": role,
                "tier": tier,                       # ← core | standard (stacking gate)
                "job_title": job.title,
                "job_url": job.url,
                "location": loc or None,
                "city": city,
                "state": state,
                "country": "US",
                "date_published": job.postedDate,
                "age": job.postedTimeAgo,
                "applicants": job.applicationsCount,
                "experience_level": job.experienceLevel,
                "contract_type": job.contractType,
                "salary": job.salary,
                "description": _clip(job.description),
                "source_board": "linkedin",
            },
        ),
        "",
    )


# ── shared helpers ────────────────────────────────────────────────────


def _composite_key(sig: RawSignal) -> tuple[str, str, str]:
    """Cross-board identity: same role at same company/city = same posting."""
    p = sig.payload
    return (
        sig.company_key,
        slugify(p.get("job_title") or p.get("role") or ""),
        (p.get("city") or "").strip().lower(),
    )


def _board_id(board: str, job: object) -> str:
    if board == "indeed":
        return job.jobKey or _fallback_id(job)            # type: ignore[union-attr]
    return getattr(job, "id", None) or _fallback_id(job)


def _looks_rcm(title: str | None) -> bool:
    t = (title or "").lower()
    return any(k in t for k in _RCM_KEYWORDS)


def _clip(text: str | None) -> str | None:
    if not text:
        return None
    t = text.strip()
    return t if len(t) <= _JD_CLIP else t[:_JD_CLIP] + "…"


def _split_loc(loc: str) -> tuple[str | None, str | None]:
    """'Dallas, TX' → ('Dallas', 'TX'); 'Remote' → ('Remote', None)."""
    if not loc:
        return None, None
    if "," in loc:
        city, rest = loc.split(",", 1)
        return city.strip() or None, rest.strip().split()[0] if rest.strip() else None
    return loc.strip() or None, None


def _indeed_domain(job: IndeedJob) -> str | None:
    """Best-effort real company domain: corporate website, else a non-board
    email domain. Indeed's companyUrl is its own cmp page, so it's useless as a
    domain — the Claude qualifier resolves the site by name when this is None.
    """
    links = job.companyLinks or {}
    site = links.get("corporateWebsite") or links.get("website")
    dom = _domain_from_url(site)
    if dom:
        return dom
    for email in job.emails or []:
        cand = (email.split("@")[-1] or "").strip().lower()
        if cand and "." in cand and not cand.endswith((".jobs", "indeed.com")):
            cleaned = clean_domain(cand)
            if cleaned:
                return cleaned
    return None


# Leading host labels that are an ATS/careers subdomain, not the real org
# domain — strip them so jobs.clevelandclinic.org → clevelandclinic.org.
_JOB_SUBDOMAINS = {
    "www", "jobs", "job", "careers", "career", "recruiting", "recruit",
    "apply", "workforcenow", "talent",
}


def _domain_from_url(url: str | None) -> str | None:
    if not url:
        return None
    host = re.sub(r"^https?://", "", url.strip().lower()).split("/")[0].split("?")[0]
    labels = host.split(".")
    while len(labels) > 2 and labels[0] in _JOB_SUBDOMAINS:
        labels = labels[1:]
    return clean_domain(".".join(labels))


def _state(loc: dict) -> str | None:
    short = loc.get("formattedAddressShort") or ""
    if "," in short:
        return short.rsplit(",", 1)[-1].strip() or None
    return loc.get("region") or loc.get("state")


def _truthy(v: object) -> bool:
    return str(v).strip().lower() in ("true", "1", "yes")


def _salary_text(salary: object) -> str | None:
    if isinstance(salary, dict):
        return salary.get("salaryText")
    if isinstance(salary, str) and salary.strip():
        return salary
    return None


def _fallback_id(job: object) -> str:
    company = slugify(getattr(job, "companyName", None) or "unknown")
    title = slugify(getattr(job, "title", None) or "role")
    date = getattr(job, "datePublished", None) or getattr(job, "postedDate", None) or "na"
    return f"{company}__{title}::{date}"


def _since_to_from_days(since: datetime) -> str:
    """Map a cutoff to Indeed's fromDays enum ('1','3','7','14')."""
    days = max(1, (datetime.now(UTC) - since).days)
    if days <= 1:
        return "1"
    if days <= 3:
        return "3"
    if days <= 7:
        return "7"
    return "14"


def _since_to_days(since: datetime) -> int:
    """Map a cutoff to LinkedIn's coarse window (1 / 7 / 30 days)."""
    days = max(1, (datetime.now(UTC) - since).days)
    if days <= 1:
        return 1
    if days <= 7:
        return 7
    return 30


# ── manual CLI trigger ────────────────────────────────────────────────

if __name__ == "__main__":
    import asyncio
    import sys
    from datetime import timedelta

    from dotenv import load_dotenv

    load_dotenv(override=True)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s  %(levelname)-7s  %(message)s")

    # Usage: python -m auto_search.connectors.job_postings [days] [rows] [sources]
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    rows = int(sys.argv[2]) if len(sys.argv) > 2 else 10
    sources = tuple(sys.argv[3].split(",")) if len(sys.argv) > 3 else DEFAULT_SOURCES
    since = datetime.now(UTC) - timedelta(days=days)
    n_titles = len(ESSENTIAL_RCM_TITLES)
    print(f"\nJobs ({', '.join(sources)}) — last {days}d, {rows} rows × {n_titles} "
          f"titles × {len(sources)} boards ≈ {rows * n_titles * len(sources)} "
          f"row-credits\n")

    async def _run() -> None:
        connector = JobPostingsConnector(max_rows=rows, sources=sources)
        by_company: dict[str, list[RawSignal]] = {}
        async for sig in connector.pull(since=since):
            by_company.setdefault(sig.company_key, []).append(sig)

        total = sum(len(v) for v in by_company.values())
        print(f"\n{total} postings across {len(by_company)} companies:\n")
        for sigs in sorted(by_company.values(), key=len, reverse=True):
            name = sigs[0].company_name_raw
            roles = Counter(s.payload["role"] for s in sigs)
            chips = ", ".join(f"{n} {r}" for r, n in roles.most_common())
            boards = "/".join(sorted({s.payload["source_board"] for s in sigs}))
            dom = sigs[0].company_domain_raw or "—"
            print(f"  {name[:30]:30} [{dom:24}] {boards:14} {chips}")

    asyncio.run(_run())
