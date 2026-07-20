# ABM Intelligence Platform

Account-based marketing intelligence for US healthcare. The platform finds healthcare
companies showing buying signals, scores how well each one fits the ICP, tracks how target
accounts engage across every channel, and routes the hottest accounts to sales. One
pipeline, two lenses: deterministic **intent** ranking and deep AI **fit** scoring.

## What it does

| Phase | Capability | Status |
|---|---|---|
| Discovery | Signal scan (hiring, leadership changes, funding, layoffs, M&A, executive social engagement), ICP qualification, deterministic intent ranking | Live |
| Scored | Deep AI fit scoring per segment rubric (health systems, specialties, payers), QA spot-check, warm-intro paths, research dossiers | Live |
| News | Industry monitoring (CMS rules, prior auth, denials, RCM AI) turned into scored sales plays | Live |
| Watch list | Low-intent parking with TTL-based promotion back into Discovery | Live |
| Engagement | Cross-channel heat scoring (SFDC, Reply.io, SmartLead, HeyReach, podcast, LinkedIn ads), Hot/Warm tiers, Slack activation handoff to AE/SDR | Live |
| Campaign automation | Auto-enrollment of scored, in-market accounts into Reply.io sequences (the write side of the loop) | Built, ships dark |

## Architecture at a glance

Four Railway services, one Postgres database, one codebase.

| Service | Role | Schedule |
|---|---|---|
| `discovery-api` | FastAPI app serving the API and the operator UI | Always on |
| `engagement-preview` | Second FastAPI instance for engagement work | Always on |
| `discovery-cron` | Daily discovery run (connectors, qualification, intent scoring) | 12:30 UTC, Mon-Fri |
| `linkedin-tofu-cron` | LinkedIn ad-engagement capture into the TOFU lead flow | Every 15 minutes |

- **Backend:** FastAPI (sync handlers), Python 3.11+. Entry point `auto_search/api/app.py`.
- **Frontend:** React 18 in `web/discovery/`, transpiled in the browser (no build step).
- **Storage:** Postgres 16 in production (`DATABASE_URL`); a JSON-file repository backs
  local development and tests. Every repository method exists in both implementations.
- **Scoring:** Anthropic Claude for qualification, fit scoring, and news triage. Intent
  scoring is deterministic (no LLM in the ranking path).
- **Signal sources:** Apify job boards, SignalBase (leadership, M&A, funding), WarnTracker
  (layoffs), LinkedIn engagement, Google News RSS.
- **Engagement sources:** Salesforce, Reply.io, SmartLead, HeyReach, podcast subscriptions,
  LinkedIn ads. Webhook receivers are shared-secret gated; everything else sits behind
  HTTP Basic auth, which is required for the app to start in production.
- **Cost controls:** every paid LLM and Apify call passes through the spend guard;
  monthly budgets hard-stop overruns. See `docs/SPEND_GUARDRAILS.md`.

## Quickstart

```bash
# Install
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
pip install -e ".[dev]"
playwright install chromium          # only needed for the WARN connector + UI tests

# Configure (all keys load from the environment; nothing is hardcoded)
cp .env.example .env                 # then fill in ANTHROPIC_API_KEY etc.

# Database (optional locally; unset DATABASE_URL falls back to a JSON file)
createdb abm_discovery
psql -d abm_discovery -f auto_search/db/schema.sql

# Run the app  ->  http://127.0.0.1:8000
uvicorn auto_search.api.app:app --port 8000
```

Ad-hoc CLI scoring of a single company:

```bash
python scorer.py "Beacon Health System" --segment hs
```

## Development

```bash
ruff check .                         # lint (gates CI)
pytest                               # unit tests, no network or LLM calls
python3 -m pytest tests/ui/test_ui_smoke.py   # Playwright UI smoke (needs local Postgres)
```

CI (`.github/workflows/ci.yml`) runs ruff and pytest on every push and pull request.
Branch per feature, one small PR per feature. Every notable bug gets a regression test
and an entry in `evals/bugs.json`.

Deploys go through one path:

```bash
./scripts/ship.sh
```

It ships all four services with a shared build stamp, then verifies the stamp is actually
serving before declaring success, so web and cron services can never drift apart.

## Where to read next

- `AGENTS.md` - how to work in this repo: hard constraints, the build pipeline, the module map
- `docs/RULES.md` - the scoring and notification rules ledger (every gate, owner, and enforcement point)
- `evals/bugs.json` - the bug registry: every shipped bug, the test that now guards it, the lesson
- `docs/ENGAGEMENT_ARCHITECTURE.md` and `docs/CAMPAIGN_AUTOMATION_ARCHITECTURE.md` - phase specs
- `docs/discovery.md`, `docs/scored.md`, `docs/engagement.md`, `docs/news.md`, `docs/watchlist.md` - plain-language feature guides per panel
- `ONBOARDING.md` - full environment walkthrough
- `KNOWN_ISSUES.md` - operational caveats
