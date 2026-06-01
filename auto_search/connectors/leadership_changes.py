"""Leadership-change connector — SignalBase (real-time job changes).

Detects US healthcare leaders who STARTED a role recently — a buying signal
(new exec = budget review + openness to change). Deterministic: every record
carries an `occurredAt` timestamp, so recency is a date comparison, not a
guess. No news scraping, no LLM in the detection path.

Filtering strategy
------------------
Server-side (SignalBase, confirmed working): country = US, seniority in
{c_level, vp, director}, recent date. We also send `industry` — it's ignored
by the run-sync path today but harmless, and helps if/when called via standby.

Client-side (the authority here):
  • healthcare PROVIDER/payer industry (excludes pharma/biotech/device, which
    are ICP disqualifiers),
  • the role title matches Galyna's target list (keyword match on newRole),
  • occurredAt >= since.

Because results come newest-first, the connector stops paging as soon as it
crosses the date cutoff — keeping Apify credit spend (1 per page) minimal.

Maps to Galyna's target roles: CEO/CFO/COO, Chief Digital/IT/Innovation,
rev-cycle & finance leaders, population-health leaders, Chief Medical/Nursing.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from collections.abc import AsyncIterator
from datetime import UTC, datetime

from auto_search.clients.signalbase import JobChangeRecord, SignalBaseClient
from auto_search.models import RawSignal

logger = logging.getLogger(__name__)

# Galyna's target roles, sent to SignalBase's free-text `positions` filter.
# This is our strongest server-side narrowing: `positions` partial-matches the
# new role, so most returned rows are already a role we care about (verified:
# this cuts ~1M changes to a few hundred). We still confirm the title and
# healthcare industry client-side.
_TARGET_POSITIONS = ",".join((
    "chief executive officer",
    "chief financial officer",
    "chief operating officer",
    "chief medical officer",
    "chief nursing officer",
    "chief information officer",
    "chief digital officer",
    "chief innovation officer",
    "chief strategy officer",
    "revenue cycle",
    "population health",
    "vp finance",
))

# Sent as a hint (ignored by run-sync today, used if called via standby).
# Provider/payer industries only — pharma/biotech/device are ICP disqualifiers.
_HINT_INDUSTRIES = "Hospitals and Health Care,Medical Practices,Mental Health Care"

# Client-side healthcare gate. Match provider/payer industry strings…
# NOTE: use "hospitals" (plural, as in LinkedIn's "Hospitals and Health Care")
# not "hospital" — the latter is a substring of "Hospitality" and would let
# resorts/bars through.
_HEALTHCARE_INCLUDE = (
    "health care", "hospitals", "medical practice", "mental health",
    "behavioral health", "nursing", "home health", "ambulatory",
    "health system", "health insurance",
)
# …but exclude industries that look healthcare-ish but are out of ICP:
# life sciences (pharma/biotech/device) and hospitality (the "hospital" trap).
_HEALTHCARE_EXCLUDE = (
    "pharmaceutical", "biotechnology", "medical device", "medical equipment",
    "research services", "hospitality",
)

# Multi-word title phrases matching Galyna's target roles (substring match
# on the lowercased newRole). These are specific enough not to misfire.
_TITLE_PHRASES = (
    "chief executive", "chief financial", "chief operating",
    "chief medical", "chief nursing", "chief clinical",
    "chief information", "chief digital", "chief innovation",
    "chief strategy", "chief transformation",
    "finance", "financial", "revenue cycle", "revenue integrity",
    "population health",
    "medical officer", "nursing officer", "clinical officer",
    "informatics", "information officer",
    "digital health", "digital transformation",
)

# C-suite abbreviations matched as WHOLE WORDS via \b boundaries, so "COO,"
# and "COO/Founder" match but "COOrdinator" and "COOk" do not.
_CSUITE_ABBR_RE = re.compile(r"\b(ceo|cfo|coo|cmo|cno|cio|cdo)\b", re.IGNORECASE)

# Non-leadership markers. The free-text `positions` filter matches "revenue
# cycle" against analysts/reps/leads too, so we drop these — UNLESS the title
# is C-suite (e.g. "Assistant Chief Nursing Officer" is still a CNO-track role).
_NON_LEADER_MARKERS = (
    "analyst", "representative", "coordinator", "specialist", "technician",
    "clerk", "intern", "junior", "entry level", "team lead", "associate",
)


class LeadershipChangesConnector:
    """Pull recent US healthcare leadership changes from SignalBase."""

    source_name = "signalbase_leadership"
    signal_types = ["leadership_change"]
    default_cron = "0 7 * * *"  # 07:00 UTC daily

    def __init__(
        self,
        *,
        client: SignalBaseClient | None = None,
        max_pages: int = 3,
    ) -> None:
        self._client = client or SignalBaseClient()
        self._max_pages = max_pages

    async def pull(self, since: datetime) -> AsyncIterator[RawSignal]:
        """Yield a leadership_change signal per US healthcare leader who
        started a targeted role on/after `since`.
        """
        drops: Counter[str] = Counter()
        yielded = 0
        crossed_cutoff = False

        records = self._client.iter_job_changes(
            positions=_TARGET_POSITIONS,        # primary server-side narrowing
            countries="US",
            date_preset=_since_to_preset(since),
            industry=_HINT_INDUSTRIES,          # hint only (ignored by run-sync)
            max_pages=self._max_pages,
        )

        async for rec in records:
            signal, reason = _record_to_signal(rec, since)
            if signal is None:
                drops[reason] += 1
                # Feed is newest-first: once we're reading records older than
                # the cutoff, everything after is older too — stop paging.
                if reason == "before_window":
                    crossed_cutoff = True
                    break
                continue
            yielded += 1
            yield signal

        logger.info(
            "signalbase_leadership pull done — yielded=%d%s",
            yielded, " (stopped at date cutoff)" if crossed_cutoff else "",
        )
        for reason, n in drops.most_common():
            logger.info("  dropped %d  %s", n, reason)


# ── record → signal ───────────────────────────────────────────────────


def _record_to_signal(
    rec: JobChangeRecord, since: datetime
) -> tuple[RawSignal | None, str]:
    """Map a SignalBase job change to a leadership_change signal, or drop it."""
    company = (rec.companyName or "").strip()
    if not company:
        return None, "missing_company"

    observed_at = _parse_dt(rec.occurredAt)
    if observed_at is None:
        return None, "unparseable_date"
    if observed_at < since:
        return None, "before_window"

    if (rec.companyCountry or "").upper() not in ("US", ""):
        return None, "non_us"

    if not _is_healthcare(rec.companyIndustry):
        return None, "not_healthcare"

    if not _is_target_title(rec.newRole):
        return None, "role_not_targeted"

    return (
        RawSignal(
            source="signalbase_leadership",
            source_external_id=rec.signalId or _fallback_id(rec, observed_at),
            signal_type="leadership_change",
            company_name_raw=company,
            company_domain_raw=_clean_domain(rec.companyWebsite),
            observed_at=observed_at,
            signal_strength=_signal_strength(rec.newRole),
            payload={
                "person_name": rec.personName,
                "new_role": rec.newRole,
                "person_linkedin": rec.personLinkedinUrl,
                "company_industry": rec.companyIndustry,
                "company_employees": rec.companyEmployeeCount,
                "occurred_at": rec.occurredAt,
                "post_content": rec.postContent,
            },
        ),
        "",
    )


def _is_healthcare(industry: str | None) -> bool:
    """True for provider/payer industries, False for pharma/biotech/device."""
    ind = (industry or "").lower()
    if not ind:
        return False
    if any(x in ind for x in _HEALTHCARE_EXCLUDE):
        return False
    return any(x in ind for x in _HEALTHCARE_INCLUDE)


def _is_csuite(role: str | None) -> bool:
    """True if the title is C-level: contains 'chief' or a C-suite abbrev."""
    title = (role or "").lower()
    return "chief" in title or bool(_CSUITE_ABBR_RE.search(title))


def _is_target_title(role: str | None) -> bool:
    """C-suite always qualifies. Otherwise require a target phrase AND no
    non-leadership marker (so "Head of Revenue Cycle" passes but "Revenue
    Cycle Analyst" / "...Team Lead" / "junior..." do not).
    """
    if _is_csuite(role):
        return True
    title = (role or "").lower()
    if any(m in title for m in _NON_LEADER_MARKERS):
        return False
    return any(p in title for p in _TITLE_PHRASES)


def _signal_strength(role: str | None) -> float:
    """C-suite changes are the strongest signal; VP/director below."""
    if _is_csuite(role):
        return 0.90
    title = (role or "").lower()
    if "vice president" in title or _word(title, "vp"):
        return 0.75
    return 0.65


def _word(text: str, w: str) -> bool:
    return re.search(rf"\b{re.escape(w)}\b", text) is not None


def _clean_domain(website: str | None) -> str | None:
    """SignalBase sometimes returns vanity links (e.g. 'co.jll/4ozae4w').
    Keep only plausible bare domains; the qualifier finds the real site anyway.
    """
    w = (website or "").strip().lower()
    if not w or "/" in w or " " in w or "." not in w:
        return None
    return w


def _fallback_id(rec: JobChangeRecord, observed_at: datetime) -> str:
    name = (rec.personName or "unknown").lower().replace(" ", "_")
    comp = "".join(c if c.isalnum() else "_" for c in (rec.companyName or "")).strip("_")
    return f"{name}::{comp}::{observed_at.date().isoformat()}"


def _parse_dt(s: str | None) -> datetime | None:
    s = (s or "").strip()
    if not s:
        return None
    # ISO-8601, typically '2026-05-30T01:19:36.672Z'
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d", "%Y-%m"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    return None


def _since_to_preset(since: datetime) -> str:
    """Map a cutoff to the smallest SignalBase date_preset that covers it.

    Coarse server hint only — the connector's occurredAt >= since check is the
    real authority, so over-covering here just means a few extra client drops.
    """
    days = max(0, (datetime.now(UTC) - since).days)
    if days <= 1:
        return "today"
    if days <= 7:
        return "last_7d"
    if days <= 14:
        return "last_14d"
    if days <= 30:
        return "last_30d"
    if days <= 60:
        return "last_60d"
    if days <= 90:
        return "last_90d"
    if days <= 180:
        return "last_6m"
    return "last_1y"


# ── manual CLI trigger ────────────────────────────────────────────────

if __name__ == "__main__":
    import asyncio
    import sys
    from datetime import timedelta

    from dotenv import load_dotenv

    load_dotenv()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s  %(levelname)-7s  %(message)s")
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    since = datetime.now(UTC) - timedelta(days=days)
    pages = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    print(f"\nSignalBase leadership changes since {since.date()} ({days}d), "
          f"max {pages} page(s) ≈ {pages} Apify credit(s)\n")

    async def _run() -> None:
        connector = LeadershipChangesConnector(max_pages=pages)
        n = 0
        async for sig in connector.pull(since=since):
            n += 1
            p = sig.payload
            print(f"  {n:>3} {str(p['new_role'])[:34]:34} @ {sig.company_name_raw[:26]:26}"
                  f" {str(p['company_industry'])[:20]:20} {str(p['occurred_at'])[:10]} "
                  f"s={sig.signal_strength}")
        print(f"\nDone — {n} healthcare leadership signals.\n")

    asyncio.run(_run())
