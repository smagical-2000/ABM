"""Leadership-change connector — SignalBase (real-time job changes).

Detects US healthcare leaders who STARTED a role recently — a buying signal
(new exec = budget review + openness to change). Deterministic: every record
carries an `occurredAt` timestamp, so recency is a date comparison, not a
guess. No news scraping, no LLM in the detection path.

Filtering strategy
------------------
Server-side (SignalBase): country = US, `seniorities` in {c_level, vp,
director}, recent date. We used to narrow via the free-text `positions`
filter instead (partial-match on the new role) — that feed collapsed ~Jul 1
2026 (0 rows returned since; 2026-07-23 audit) and `categories` is a no-op on
this actor, so seniority is the only live server-side narrowing now. It's
broader than `positions` was, but the client-side gates below stay the
authority, so the widening only costs a few extra record-credits per pull.

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
from auto_search.healthcare import CATEGORIES_FILTER, is_healthcare_provider
from auto_search.models import RawSignal
from auto_search.normalize import clean_domain, parse_iso_datetime

logger = logging.getLogger(__name__)

# Server-side seniority filter — the ONE narrowing that still works on this
# actor. The old free-text `positions` filter (Galyna's 12 target roles) was
# our strongest narrowing until its feed collapsed ~Jul 1 2026: it silently
# started returning 0 rows, so the daily leg produced nothing for 3+ weeks
# (2026-07-23 audit). Seniority-level filtering is confirmed live on
# SignalBaseClient.iter_job_changes; the title phrases below do the precise
# role targeting client-side.
_TARGET_SENIORITIES = "c_level,vp,director"

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

# Non-leadership markers. The server-side narrowing (seniorities since
# 2026-07-23; free-text `positions` before) still lets analysts/reps/leads
# through — e.g. LinkedIn tags "Revenue Cycle Team Lead" director-level — so we
# drop these — UNLESS the title is C-suite (e.g. "Assistant Chief Nursing
# Officer" is still a CNO-track role).
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
        max_pages: int = 1,
        per_page: int = 5,
    ) -> None:
        # COST = SignalBase bills per RECORD returned (~$30/1000). So the spend
        # of one pull ≈ per_page × max_pages. Keep per_page small for testing.
        self._client = client or SignalBaseClient()
        self._max_pages = max_pages
        self._per_page = per_page

    async def pull(self, since: datetime) -> AsyncIterator[RawSignal]:
        """Yield a leadership_change signal per US healthcare leader who
        started a targeted role on/after `since`.
        """
        drops: Counter[str] = Counter()
        yielded = 0
        crossed_cutoff = False

        # 2026-07-23: `positions` free-text narrowing REMOVED — its feed
        # collapsed ~Jul 1 (0 rows since), and `categories` is a no-op on this
        # actor, so `seniorities` is the only server-side filter that bites.
        # `categories` still sent: harmless today, useful if the actor fixes it.
        # The title (_is_target_title) + healthcare gates in _record_to_signal
        # remain the authority on what actually yields.
        records = self._client.iter_job_changes(
            seniorities=_TARGET_SENIORITIES,    # c_level,vp,director — live narrowing
            countries="US",
            categories=CATEGORIES_FILTER,       # no-op on this actor (kept, harmless)
            date_preset=_since_to_preset(since),
            per_page=self._per_page,
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

    observed_at = parse_iso_datetime(rec.occurredAt)
    if observed_at is None:
        return None, "unparseable_date"
    if observed_at < since:
        return None, "before_window"

    if (rec.companyCountry or "").upper() not in ("US", ""):
        return None, "non_us"

    if not is_healthcare_provider(rec.companyIndustry, rec.companySubcategory):
        return None, "not_healthcare"

    if not _is_target_title(rec.newRole):
        return None, "role_not_targeted"

    return (
        RawSignal(
            source="signalbase_leadership",
            source_external_id=rec.signalId or _fallback_id(rec, observed_at),
            signal_type="leadership_change",
            company_name_raw=company,
            company_domain_raw=clean_domain(rec.companyWebsite),
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


def _fallback_id(rec: JobChangeRecord, observed_at: datetime) -> str:
    name = (rec.personName or "unknown").lower().replace(" ", "_")
    comp = "".join(c if c.isalnum() else "_" for c in (rec.companyName or "")).strip("_")
    return f"{name}::{comp}::{observed_at.date().isoformat()}"


def _since_to_preset(since: datetime) -> str:
    """Map a cutoff to the smallest SignalBase date_preset that covers it.

    Coarse server hint only — the connector's occurredAt >= since check is the
    real authority, so over-covering here just means a few extra client drops.

    FLOOR = last_7d, never "today" (2026-07-23 audit): "today" server-side is
    00:00 UTC → request time, so the 12:31Z cron covered only ~37% of the week
    (5 weekday runs × 12.5h / 168h) — an exec start announced at 15:00 UTC was
    structurally invisible to every run. Over-covering is free client-side.
    """
    days = max(0, (datetime.now(UTC) - since).days)
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

    load_dotenv(override=True)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s  %(levelname)-7s  %(message)s")
    # Usage: python -m auto_search.connectors.leadership_changes [days] [limit] [pages]
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 5    # records/call = credits
    pages = int(sys.argv[3]) if len(sys.argv) > 3 else 1
    since = datetime.now(UTC) - timedelta(days=days)
    print(f"\nSignalBase leadership changes since {since.date()} ({days}d) — "
          f"≈ {limit * pages} record-credit(s) (limit {limit} × {pages} page)\n")

    async def _run() -> None:
        connector = LeadershipChangesConnector(max_pages=pages, per_page=limit)
        n = 0
        async for sig in connector.pull(since=since):
            n += 1
            p = sig.payload
            print(f"  {n:>3} {str(p['new_role'])[:34]:34} @ {sig.company_name_raw[:26]:26}"
                  f" {str(p['company_industry'])[:20]:20} {str(p['occurred_at'])[:10]} "
                  f"s={sig.signal_strength}")
        print(f"\nDone — {n} healthcare leadership signals.\n")

    asyncio.run(_run())
