# Magical ABM

Three parts:

1. **CLI Account Scorer** (`scorer.py`) — runs Galyna's ABM scoring frameworks
   against a named company. Claude (Sonnet) with live web search.
2. **Auto Search** (`auto_search/`) — discovery pipeline that finds healthcare
   companies showing buying signals (layoffs, leadership changes, M&A),
   qualifies them against the ICP, and dedupes them. See
   [`auto_search/README.md`](auto_search/README.md).
3. **Discovery Panel** (`auto_search/api/` + `web/discovery/`) — FastAPI +
   React UI where Galyna reviews qualified companies and promotes/rejects/defers.

---

## Run the live Discovery app

```bash
# 1. Postgres (local). Railway: just set DATABASE_URL to the platform URL.
brew install postgresql@16 && brew services start postgresql@16
createdb abm_discovery
psql -d abm_discovery -f auto_search/db/schema.sql

# 2. Point the app at it (unset DATABASE_URL → falls back to a JSON file)
echo "DATABASE_URL=postgresql://localhost/abm_discovery" >> .env

# 3. Populate real accounts (costs: SignalBase per record + Sonnet per company)
python scripts/run_discovery.py --only leadership --days 60 --limit 25
python scripts/run_discovery.py --panel                 # inspect (no cost)

# 4. Serve the API + UI  →  http://127.0.0.1:8000
uvicorn auto_search.api.app:app --port 8000
```

Storage is chosen by `get_repository()`: Postgres when `DATABASE_URL` is set,
else a JSON file (zero-infra). The UI talks only to the API; the API talks only
to `ReviewService`; the service talks only to the repository protocol — so the
JSON ↔ Postgres swap, or a future hosted deploy, never touches the layers above.

---

## Setup (one-time)

```bash
cd /Users/sunnydsouza/projects/abm-scorer

# 1. Virtual environment
python3 -m venv venv
source venv/bin/activate

# 2. Dependencies
pip install -r requirements.txt

# 3. Browser for the Auto Search connector (warntracker is client-rendered)
playwright install chromium

# 4. Secrets
cp .env.example .env        # then edit .env with your real ANTHROPIC_API_KEY
```

Dev tooling (lint + tests):

```bash
pip install -e ".[dev]"
ruff check .
pytest
```

---

## Usage

```bash
# Score an Ortho / Behavioral Health account (30-point framework)
python scorer.py "OrthoIndy" --segment specialties

# Score a Health System (27-point Beacon-modeled framework)
python scorer.py "Beacon Health System" --segment hs

# Score a Payer (30-point framework, Tier 1/2/3)
python scorer.py "Centene" --segment payer

# Preview the prompt without calling the API
python scorer.py "OrthoIndy" --segment specialties --dry-run
```

Output is streamed to terminal and saved to `outputs/<segment>_<company>_<timestamp>.md`.

---

## What it does

For each company:

1. Loads the segment-specific scoring prompt (exact match to Galyna's prompts)
2. Sends it to Claude Opus 4.7 with web search enabled
3. Claude researches the company across the public web (news, press releases, LinkedIn, careers pages, CMS data, etc.)
4. Returns a structured scored report with category sub-scores, total, tier, decision-makers, pain points, messaging angles

---

## Scoring frameworks

| Segment | Scale | Tiers |
|---|---|---|
| `specialties` (Ortho / BH) | 30 pts (Firmo + Techno + Intent, 10 each) | High 24-30 / Med 18-23 / Low <18 |
| `payer` | 30 pts (Firmo + Techno + Intent, 10 each) | T1: 22+ / T2: 18-21 / T3: 15-17 |
| `hs` (Health Systems) | 27 pts (NPR 10 + EMR 5 + Comp 4 + Pain 5 + Tech 2 + Leadership 1) | T1: 22-27 / T2: 16-21 / T3: 10-15 / T4: <10 or NPR>$3.5B |

Prompts live in `prompts/` — edit those files to tweak scoring criteria, not the Python.

---

## CLI scorer limitations

- Single company per run (no batch mode in the CLI)
- Output is markdown only (not pushed to Notion / Salesforce yet)
- Web search has a 5-search-per-request default cap

Deduplication + storage now live in the **Auto Search** module
(`auto_search/`), not the CLI. The CLI remains a deliberately simple
one-shot tool for ad-hoc scoring.
