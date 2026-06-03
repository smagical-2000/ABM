"""Job-postings connector — Indeed (primary), via the Apify scraper.

The single most on-the-nose buying signal for Magical: a US healthcare provider
posting revenue-cycle roles (coders, billers, AR/denials/prior-auth staff). That
manual RCM headcount is exactly what agentic automation replaces — "automate
these instead of hiring eight people."

How it works
------------
SignalBase's hiring feed has no title filter, so we search Indeed's quoted
title field for each ESSENTIAL RCM role, last-24h, a few rows each. Every
posting becomes a `job_posting` signal about the HIRING company.

Two-layer qualification (the user's "are these the exact jobs we need?")
    1. ROLE  — the quoted title search + an RCM-keyword sanity gate keep only
       genuine revenue-cycle postings.
    2. EMPLOYER — we do NOT pre-gate on industry (Indeed's industry field is
       almost always null). Instead each company flows through the existing
       Claude website qualifier, which already disqualifies RCM *vendors*,
       labs, staffing firms and tech — keeping only provider/payer ICP.

Volume > seniority
------------------
Each posting is its own signal, tagged with a short `role` bucket (Coder,
Biller, Denials…). The pipeline groups by company, so a hospital posting eight
coder roles surfaces as one company with eight job_posting signals — the count,
grouped by role, is the pain-intensity readout the UI renders ("3 Coder jobs").

Cost: the Indeed actor bills per row. Keep `max_rows` small and search only the
essential titles below.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from collections.abc import AsyncIterator
from datetime import UTC, datetime

from auto_search.clients.apify_jobs import ApifyJobsClient, IndeedJob
from auto_search.models import RawSignal
from auto_search.normalize import clean_domain, parse_iso_datetime, slugify

logger = logging.getLogger(__name__)

# The essential RCM titles to search, each as (quoted query, role bucket,
# base signal strength). Order high-value roles first. The role bucket is what
# the UI groups on ("3 Coder jobs"). Quotes give Indeed an exact-phrase match.
EssentialTitle = tuple[str, str, float]
ESSENTIAL_RCM_TITLES: list[EssentialTitle] = [
    ('"prior authorization specialist"', "Prior Auth", 0.85),
    ('"insurance verification specialist"', "Eligibility", 0.85),
    ('"denials specialist"', "Denials", 0.85),
    ('"medical biller"', "Biller", 0.80),
    # Healthcare-specific AR/collections role. NOTE: a bare "accounts receivable
    # specialist" search is cross-industry (utilities/law/manufacturing) and
    # wastes qualifier calls — the patient-account title keeps it provider-bound.
    ('"patient account representative"', "AR / Collections", 0.80),
    ('"medical coder"', "Coder", 0.78),
    ('"revenue cycle"', "Revenue Cycle", 0.75),
    ('"patient access representative"', "Patient Access", 0.72),
]

# RCM-keyword sanity gate: a returned title must contain one of these to count
# as an actual revenue-cycle role (drops the occasional off-topic match Indeed
# returns even for a quoted query). Broad on purpose — precision comes from the
# quoted search; this just removes obvious noise.
_RCM_KEYWORDS = (
    "coder", "coding", "biller", "billing", "revenue cycle", "revenue integrity",
    "accounts receivable", "a/r", "ar specialist", "ar follow",
    "denial", "prior auth", "authorization", "pre-cert", "precert",
    "insurance verification", "eligibility", "claims", "charge entry",
    "charge capture", "patient access", "patient account", "reimbursement",
    "collections", "payment posting", "rcm",
)


class JobPostingsConnector:
    """Pull recent US healthcare RCM job postings from Indeed."""

    source_name = "indeed"
    signal_types = ["job_posting"]
    default_cron = "0 10 * * *"  # 10:00 UTC daily

    def __init__(
        self,
        *,
        client: ApifyJobsClient | None = None,
        titles: list[EssentialTitle] | None = None,
        max_rows: int = 10,
        country: str = "us",
    ) -> None:
        self._client = client or ApifyJobsClient()
        self._titles = titles or ESSENTIAL_RCM_TITLES
        self._max_rows = max_rows
        self._country = country

    async def pull(self, since: datetime) -> AsyncIterator[RawSignal]:
        """Yield a job_posting signal per US healthcare RCM opening on/after
        `since`. One signal per posting; deduped by Indeed jobKey across the
        title queries (a posting can match two searches).
        """
        from_days = _since_to_from_days(since)
        drops: Counter[str] = Counter()
        seen: set[str] = set()
        yielded = 0

        for query, role, strength in self._titles:
            try:
                jobs = await self._client.search_indeed(
                    query, country=self._country,
                    from_days=from_days, max_rows=self._max_rows,
                )
            except Exception as e:  # noqa: BLE001 — one title must not kill the run
                logger.warning("indeed search %s failed: %s", query, e)
                drops[f"search_error:{query}"] += 1
                continue

            for job in jobs:
                key = job.jobKey or _fallback_id(job)
                if key in seen:
                    drops["dup_jobkey"] += 1
                    continue
                seen.add(key)
                signal, reason = _job_to_signal(job, role, strength, since, key)
                if signal is None:
                    drops[reason] += 1
                    continue
                yielded += 1
                yield signal

        logger.info("indeed job_postings pull done — yielded=%d", yielded)
        for reason, n in drops.most_common():
            logger.info("  dropped %d  %s", n, reason)


# ── record → signal ───────────────────────────────────────────────────


def _job_to_signal(
    job: IndeedJob, role: str, strength: float, since: datetime, key: str
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
                "company_website": _indeed_domain(job),
                "company_indeed_url": job.companyUrl,
                "source_board": "indeed",
            },
        ),
        "",
    )


def _looks_rcm(title: str | None) -> bool:
    t = (title or "").lower()
    return any(k in t for k in _RCM_KEYWORDS)


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


def _fallback_id(job: IndeedJob) -> str:
    company = slugify(job.companyName or "unknown")
    title = slugify(job.title or "role")
    return f"{company}__{title}::{job.datePublished or 'na'}"


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


# ── manual CLI trigger ────────────────────────────────────────────────

if __name__ == "__main__":
    import asyncio
    import sys
    from datetime import timedelta

    from dotenv import load_dotenv

    load_dotenv(override=True)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s  %(levelname)-7s  %(message)s")

    # Usage: python -m auto_search.connectors.job_postings [days] [rows_per_title]
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    rows = int(sys.argv[2]) if len(sys.argv) > 2 else 10
    since = datetime.now(UTC) - timedelta(days=days)
    n_titles = len(ESSENTIAL_RCM_TITLES)
    print(f"\nIndeed RCM job postings — last {days}d, {rows} rows × {n_titles} "
          f"titles ≈ {rows * n_titles} row-credits\n")

    async def _run() -> None:
        connector = JobPostingsConnector(max_rows=rows)
        by_company: dict[str, list[RawSignal]] = {}
        async for sig in connector.pull(since=since):
            by_company.setdefault(sig.company_key, []).append(sig)

        total = sum(len(v) for v in by_company.values())
        print(f"\n{total} postings across {len(by_company)} companies:\n")
        for sigs in sorted(by_company.values(), key=len, reverse=True):
            name = sigs[0].company_name_raw
            roles = Counter(s.payload["role"] for s in sigs)
            chips = ", ".join(f"{n} {r}" for r, n in roles.most_common())
            dom = sigs[0].company_domain_raw or "—"
            print(f"  {name[:34]:34} [{dom:22}]  {chips}")

    asyncio.run(_run())
