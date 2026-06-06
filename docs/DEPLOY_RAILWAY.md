# Deploy the Discovery Panel to Railway

The web service is **API-only** (FastAPI serving `/api` + the static UI). It
does not need a browser. Storage is Railway Postgres; the app self-initialises
its schema on first boot.

Config already in the repo:
- `railway.api.json` — API start command + `/api/health` healthcheck (set as the
  `discovery-api` service config file path in Railway Settings)
- `nixpacks.toml` — Python 3.12, `pip install -r requirements.txt`, uvicorn
- schema auto-init — the API runs `schema.sql` (idempotent) on startup

---

## One-time deploy (run these yourself — `login` opens a browser)

```bash
cd /Users/sunnydsouza/projects/abm-scorer

railway login                       # opens browser to authenticate
railway init                        # create a project (pick a name)

# Add a Postgres database to the project
railway add --database postgres     # injects DATABASE_URL into the service

# Set the secrets the app needs (use your REAL, rotated keys)
railway variables --set "ANTHROPIC_API_KEY=sk-ant-..." \
                  --set "APIFY_API_KEY=apify_api_..." \
                  --set "ANTHROPIC_MODEL=claude-sonnet-4-5"

# Deploy
railway up                          # builds with nixpacks, deploys

# Get the public URL
railway domain                      # generates/show the https URL
```

Open the URL → the Discovery Panel loads. The panel is **empty at first**
(fresh DB). Populate it next.

---

## Populate the deployed database

The Railway Postgres starts empty. Two options:

**A. Run discovery against Railway from your laptop** (simplest):
```bash
# point the runner at the Railway DB for one run
DATABASE_URL="$(railway variables --kv | grep DATABASE_URL | cut -d= -f2-)" \
  python scripts/run_discovery.py --days 1 --no-limit
```

**B. Add the `discovery-cron` service** (hands-off daily pull). This is a SECOND
Railway service from the same repo, built from `Dockerfile.cron`, which carries
Playwright + Chromium for the layoffs connector (the web image deliberately
omits it). In the Railway dashboard:

1. New service → deploy from the same repo.
2. Settings → Build → **Dockerfile Path** = `Dockerfile.cron`.
3. Settings → **Cron Schedule** = `0 14 * * *` (14:00 UTC ≈ 10:00 America/New_York).
4. Variables: `DATABASE_URL` (same DB as web), `ANTHROPIC_API_KEY`,
   `APIFY_API_KEY`, `ANTHROPIC_MODEL`. Optional cost knobs:
   - `DISCOVERY_SIGNALBASE_MAX_PAGES` (default 50), `DISCOVERY_SIGNALBASE_PER_PAGE` (100)
   - `DISCOVERY_JOBS_MAX_ROWS` (default 200)

The service runs `scripts/run_discovery.py --days 1 --no-limit` once per tick and
exits — no HTTP port, no healthcheck. It pulls **all five sources** for the last
24h with no artificial cap, dedups, qualifies new companies, and writes the
panel. It does **NOT** auto-score; scoring stays on demand in the Scored tab.

> **Cost watch — `jobs` is the heaviest Apify burn.** It pages Indeed + LinkedIn
> up to `DISCOVERY_JOBS_MAX_ROWS`; a full daily pull costs more than any other
> connector. Lower that number or drop jobs from the run
> (`--sources layoffs,leadership,acquisitions,funding`) to cap it.

Fallback: `.github/workflows/discovery-cron.yml` runs the same command on
GitHub's runners on the same schedule if Railway cron is unavailable (set the
secrets in the repo).

---

## Two services at a glance

```
Railway project
├── web             nixpacks.toml     uvicorn :$PORT   /api/health    (no browser)
└── discovery-cron  Dockerfile.cron   run_discovery    cron 0 14 * * *  (Playwright)
        both share the same Postgres (DATABASE_URL)
```

## Local test (no Railway)

```bash
# dry run: pull + dedup, NO Claude (still hits Apify for the pull)
DATABASE_URL=... python scripts/run_discovery.py --days 1 --no-limit --no-qualify

# full run against a DB
DATABASE_URL=... python scripts/run_discovery.py --days 1 --no-limit
```

---

## Notes

- **Cost on Railway** is the same as local: SignalBase per record + Sonnet per
  company. The web service itself is cheap (small always-on container); the cron
  is the discovery-side spend driver.
- **Playwright lives only on the cron image** (`Dockerfile.cron`), never the web
  image, so the web build stays small and fast.
- **Secrets**: only in Railway variables, never in the repo. Rotate any key
  that's been pasted anywhere.
- **CORS**: the UI is served same-origin, so no CORS config needed. If you later
  split the frontend onto its own domain, set `CORS_ORIGINS` to that origin.
