# Discovery cron — production setup

The daily discovery run is a **dedicated, scheduled, run-to-completion container**
— not an in-process timer in the web app. That separation is the production
shape: its own image (Chromium for the layoffs connector), its own env, its own
logs, and it bills only for the minutes it runs. The web service is untouched.

- Image: `Dockerfile.cron` (Python 3.12 + Playwright/Chromium).
- Command: `python scripts/run_discovery.py --days 1 --no-limit` (all sources,
  last 24h, no artificial cap; env-tunable paging).
- Exit code: `0` on success (including a single source failing), `1` if **every**
  source failed — so a total failure is visibly a failed run, not a silent one.
- Idempotent: dedup (normalized company key + signal uniqueness +
  already-qualified skip) means a retry or overlap can't double-write.

## Schedule

`0 14 * * *` = **14:00 UTC = 10:00 AM America/New_York** (EDT). Railway cron is
UTC; adjust the hour for EST/DST if you care about the exact local minute.

## Option A — Railway Cron Service (recommended), via CLI (reproducible)

Run once from the repo root. Requires the `railway` CLI (`npm i -g @railway/cli`)
and `railway login`.

```bash
# 1. Link to the existing project (same project as the web service).
railway link

# 2. Create a second service for the cron and point it at Dockerfile.cron.
railway service create discovery-cron
railway service connect discovery-cron        # select it as the active service
railway variables --set "RAILWAY_DOCKERFILE_PATH=Dockerfile.cron"

# 3. Secrets + knobs (same DB as web; shared Postgres).
railway variables --set "DATABASE_URL=$DATABASE_URL" \
  --set "ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY" \
  --set "APIFY_API_KEY=$APIFY_API_KEY" \
  --set "ANTHROPIC_MODEL=claude-sonnet-4-5" \
  --set "DISCOVERY_SIGNALBASE_MAX_PAGES=50" \
  --set "DISCOVERY_SIGNALBASE_PER_PAGE=100" \
  --set "DISCOVERY_JOBS_MAX_ROWS=200"

# 4. Set the cron schedule on the service.
railway service update discovery-cron --cron "0 14 * * *"

# 5. Deploy the image.
railway up --service discovery-cron
```

> CLI flag names shift between `railway` versions. If a command differs, set the
> same things in the dashboard (Option B) — the values are what matter.

## Option B — Railway dashboard (one-time)

1. Project → **New** → **Empty Service** → name `discovery-cron`, connect this repo.
2. Service → **Settings → Build**: set **Dockerfile Path = `Dockerfile.cron`**.
3. Service → **Settings → Deploy → Cron Schedule**: `0 14 * * *`.
4. Service → **Variables**: add `DATABASE_URL`, `ANTHROPIC_API_KEY`,
   `APIFY_API_KEY`, `ANTHROPIC_MODEL`, and the optional `DISCOVERY_*` knobs.
5. **Deploy**. There is no HTTP port and no healthcheck — that's expected for a
   cron service.

## Test it immediately (don't wait for 10am)

- Railway: open the `discovery-cron` service → **Deploy** / **Run** to trigger a
  one-off run now, and watch the logs stream.
- Locally (no Chromium needed for a dry run):
  ```bash
  DATABASE_URL=... python scripts/run_discovery.py --days 1 --no-limit --no-qualify
  ```
  Drop `--no-qualify` for a real (paid) run.

## Verify a run actually happened

- **Logs**: the run prints a `RUN SUMMARY` and the per-source counts; a failed
  source is flagged, and a total failure exits non-zero (red in Railway).
- **DB heartbeat**: each source writes a `connector_runs` row.
- **Panel**: new qualified companies appear in the Discovery panel of the web app.
- **Spend**: `GET /api/scoring/stats` → `month_discovery_cost` reflects the
  qualify spend (estimate-based, `DISCOVERY_EST_QUAL_COST`).

## Alerting on failure

Because the run exits non-zero on a total failure, the platform sees a failed
deploy/run. In Railway, enable **email/webhook notifications** for the
`discovery-cron` service so a failed run pings you. (Full error tracking / Sentry
is intentionally out of scope for now.)

## Cost note

`jobs` (Indeed + LinkedIn via Apify) is the heaviest Apify burn. Tune with
`DISCOVERY_JOBS_MAX_ROWS`, or drop `jobs` from a run with
`--sources layoffs,leadership,acquisitions,funding`.

## If you ever leave Railway

The same `Dockerfile.cron` runs anywhere with a managed scheduler — these are all
equally production-grade:

- **Render**: a `render.yaml` cron job (declarative IaC in-repo).
- **GCP**: Cloud Run Job + Cloud Scheduler (fully managed, Terraform-able).
- **AWS**: ECS Scheduled Task (EventBridge) on Fargate.
- **Kubernetes**: a `CronJob` running the image.

The fallback `.github/workflows/discovery-cron.yml` also works, but GitHub only
runs schedules on the **default branch**, its scheduler is best-effort (can lag),
and it auto-disables after 60 days of repo inactivity — fine as a backup, not the
primary.
