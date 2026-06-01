# Deploy the Discovery Panel to Railway

The web service is **API-only** (FastAPI serving `/api` + the static UI). It
does not need a browser. Storage is Railway Postgres; the app self-initialises
its schema on first boot.

Config already in the repo:
- `railway.json` — start command + `/api/health` healthcheck
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
  python scripts/run_discovery.py --only leadership --days 60 --limit 25
```

**B. Add a daily cron service** (hands-off). In the Railway dashboard, add a new
service from the same repo with:
- Start command:
  `python scripts/run_discovery.py --only leadership --only acquisitions --days 1`
- A cron schedule (e.g. `0 7 * * *`)
- The same `DATABASE_URL`, `ANTHROPIC_API_KEY`, `APIFY_API_KEY`

> The cron must run **leadership + acquisitions only** (httpx). The **layoffs**
> connector uses Playwright/Chromium, which needs a heavier image — add it later
> with `playwright install chromium` in that service's build if you want it.

---

## Notes

- **Cost on Railway** is the same as local: SignalBase per record + Sonnet per
  company. The web service itself is cheap (small always-on container).
- **Secrets**: only in Railway variables, never in the repo. Rotate any key
  that's been pasted anywhere.
- **CORS**: the UI is served same-origin, so no CORS config needed. If you later
  split the frontend onto its own domain, set `CORS_ORIGINS` to that origin.
- **Promote** is still a stub (`stub-account::<key>`) until the accounts table
  lands — the button works, it just records the decision.
