# Auto Search — discovery pipeline

Finds healthcare companies showing distress/intent signals (starting with
layoffs), qualifies them against Magical's ICP using Claude + live website
research, and produces a deduped list of qualified accounts for human review.

This is **System A (discovery)** — front-of-funnel. It never runs campaigns
and never touches engagement scoring (System B). The only bridge to the rest
of the platform is *promotion*: a human turning a qualified company into an
`accounts` row.

## Flow

```
 Connector            Pipeline                 Qualifier            Repository
 ─────────            ────────                 ─────────            ──────────
 warntracker.com  →   pull + group by      →   Claude visits    →  persist
 (Playwright)         company (dedup)          the company's        verdict +
                      ↓                         website, classifies  signals
                      skip already-            vs ICP, returns
                      qualified (x-run)         structured JSON
```

**One Claude call per company, ever:**
- *within a run* — signals are grouped by normalized company name
- *across runs* — `repo.already_qualified()` skips already-decided companies

## Modules

| File | Responsibility |
|---|---|
| `normalize.py` | **Single source of truth** for dedup keys + loose int / ISO-date / domain parsing |
| `healthcare.py` | **Single source of truth** for the healthcare-ICP gate (`is_healthcare_provider`) + `categories` filter |
| `models.py` | `RawSignal`, `QualificationResult`, shared constants |
| `llm.py` | Shared Claude web_search call + JSON extraction (used by qualifier + connectors) |
| `connectors/base.py` | `SignalConnector` protocol — the contract every source implements |
| `connectors/warntracker.py` | Layoffs source — WARN notices (Playwright → `/api/sample_warn_listings`) |
| `connectors/leadership_changes.py` | Leadership source — SignalBase job changes (CXO/rev-cycle/finance) |
| `connectors/acquisitions.py` | M&A source — SignalBase acquisitions (acquired healthcare co) |
| `clients/signalbase.py` | SignalBase transport via Apify run-sync — actor-agnostic, paged, credit-capped |
| `qualifier.py` | Website-based ICP evaluation via Claude + `web_search`; writes traces |
| `pipeline.py` | Orchestration: pull → dedup → qualify → `CompanyCandidate` |
| `db/schema.sql` | Target Postgres schema (3 tables, dedup via UNIQUE constraints) |
| `db/repository.py` | Storage interface + JSON-file impl (runs without Postgres) |

## Connectors (signal sources)

| Source | Signal | Recency | Filtering |
|---|---|---|---|
| **warntracker** | Layoffs (WARN filings) | layoff/notice date | structural (US, ≥10 laid off) + website ICP |
| **leadership_changes** | New CXO / rev-cycle / finance / pop-health hires | `occurredAt` | server: `positions` + `categories` + `countries`; client: healthcare gate + title + cutoff |
| **acquisitions** | Healthcare provider/payer **acquired** (in transition) | `occurredAt` | server: `categories` + `countries`; client: healthcare gate (excl. pharma/biotech) + cutoff |

Both SignalBase connectors are **deterministic** (no news, no LLM in detection):
they push the strongest filters server-side — `positions` (leadership) or
`categories` (both) — then apply the shared `is_healthcare_provider()` gate
client-side as the authority. Crucially, that gate checks SignalBase's strict
`companySubcategory` so a biotech mislabelled "Hospitals and Health Care"
(e.g. a vaccine startup bought by pharma) is excluded. Connectors page
newest-first and STOP at the date cutoff to minimise per-record spend.

> **Cost:** SignalBase bills per RECORD (~$20–30 / 1,000), so spend ≈
> `per_page × pages`. Keep `per_page` small (default 5) when testing.

## Why website-based qualification (not keyword rules)

Industry labels on layoff trackers are unreliable — "Healthcare" might be a
wellness app, "Other" might be a hospital system. So the qualifier ignores
the label and has Claude visit the company's actual website to classify it.
The only pre-filters are *structural* (date window, ≥10 laid off).

ICP definition lives in `qualifier.py` under `ICP_SYSTEM_PROMPT`. Verdict:

```jsonc
{
  "qualified": true,
  "segment": "health_system",
  "sub_segment": "community_hospital",
  "company_type": "provider",
  "approximate_employees": 1200,
  "confidence": 0.86,
  "reasoning": "Mid-size community hospital in Ohio, ~1,200 staff…",
  "evidence_url": "https://example.org/about",
  "needs_human_review": false
}
```

Calibration: `confidence < 0.70` is forced into `needs_human_review` —
LLM confidence is not blindly trusted.

## Storage: what we keep, what we don't

**Keep** (lean): the qualified company + verdict + reasoning + evidence URL,
plus the signals that surfaced it (the "why").

**Don't keep**: the full WARN dataset, disqualified-company essays, or raw
Claude traces (those go to `data/qualifier_traces/` as files, not the DB).

Dedup is enforced by **database UNIQUE constraints**, not app code:
- `discovery_signals (source, source_external_id)` — same event never twice
- `discovery_companies (normalized_name)` — one row per company

## Adding a new signal source

1. Create `connectors/<source>.py` implementing `SignalConnector`
   (one `pull(since)` yielding `RawSignal`).
2. Done — `pipeline.run()` works with any connector unchanged.

## Testing

```bash
# Layoffs: fetch + dedup only — no LLM, no cost (--cache = saved rows, no browser)
python scripts/test_warntracker_pipeline.py --no-qualify --cache

# Leadership: detect recent US healthcare CXO/rev-cycle changes (~1 credit/page)
python scripts/test_leadership_changes.py --days 7 --pages 1

# One company by name (qualifier prompt tuning)
python scripts/test_qualifier_one.py --custom "Advanced Specialty Hospitals of Toledo"

# Unit tests (no network, no LLM, no credits)
pytest
```

## Status

- [x] Connector (warntracker layoffs, Playwright)
- [x] Connector (leadership changes, SignalBase via Apify — deterministic)
- [x] Website-based qualifier (Claude + web_search)
- [x] Dedup (within-run grouping + across-run skip)
- [x] Storage interface + JSON impl
- [ ] Postgres repository (deferred until Railway DB is connected)
- [ ] Daily cron wiring (Arq)
- [ ] More connectors: funding, ACO contracts, M&A, leadership changes
