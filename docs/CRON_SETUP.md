# Discovery cron — production setup

The weekday discovery run is a **dedicated, scheduled, run-to-completion container**
— not an in-process timer in the web app. That separation is the production
shape: its own image (Chromium for the layoffs connector), its own env, its own
logs, and it bills only for the minutes it runs. The web service is untouched.

- Image: `Dockerfile.cron` (Python 3.12 + Playwright/Chromium).
- Command: `python scripts/run_discovery.py --days 1 --no-limit` (all five
  sources including **layoffs**, last **24h**, no artificial cap; env-tunable
  paging).
- Exit code: `0` on success (including a single source failing), `1` if **every**
  source failed — so a total failure is visibly a failed run, not a silent one.
- Idempotent: dedup (normalized company key + signal uniqueness +
  already-qualified skip) means a retry or overlap can't double-write.

## Schedule

`0 14 * * 1-5` = **weekdays only**, **14:00 UTC ≈ 10:00 AM America/New_York**
(EDT). Railway cron is UTC and does not follow DST — in EST (winter) this fires
at **9:00 AM** local; bump to `0 15 * * 1-5` if you need 10:00 AM year-round
in winter.

Config lives in service-specific files (the root `railway.json` is intentionally
minimal so it does not force the API healthcheck onto the cron container):

| Service | Config file path |
|---------|------------------|
| `discovery-api` | `/railway.api.json` |
| `discovery-cron` | `/railway.cron.json` |

Set each in **Settings → Config file path** before the first deploy of that
service.

## Option A — Railway Cron Service (recommended), via CLI (reproducible)

Run once from the repo root. Requires the `railway` CLI (`npm i -g @railway/cli`)
and `railway login`.

```bash
# 1. Link to the existing project (same project as the web service).
railway link

# 2. Create a second service for the cron (empty service, same repo).
railway add --service discovery-cron
railway service link discovery-cron

# 3. In the dashboard (one-time per service):
#    discovery-api  → Settings → Config file path: /railway.api.json
#    discovery-cron → Settings → Config file path: /railway.cron.json
#    (cron file sets Dockerfile.cron + weekday schedule 0 14 * * 1-5)

# 4. Copy secrets from discovery-api (same Postgres, same Claude/Apify keys).
for key in DATABASE_URL ANTHROPIC_API_KEY ANTHROPIC_MODEL APIFY_API_KEY \
  DISCOVERY_JOBS_TITLES DISCOVERY_SIGNALBASE_MAX_PAGES DISCOVERY_SIGNALBASE_PER_PAGE \
  DISCOVERY_JOBS_MAX_ROWS; do
  val=$(railway variable list --service discovery-api --kv | grep "^${key}=" | cut -d= -f2-)
  [ -n "$val" ] && railway variable set "${key}=${val}" --service discovery-cron --skip-deploys
done

# 5. Deploy the cron image.
railway up --service discovery-cron
```

> CLI flag names shift between `railway` versions. If a command differs, set the
> same things in the dashboard (Option B) — the values are what matter.

## Option B — Railway dashboard (one-time)

1. Project → **New** → **Empty Service** → name `discovery-cron`, connect this repo.
2. Service → **Settings → Config file path**: `/railway.cron.json`
   (weekday cron + Dockerfile.cron). Or manually: **Dockerfile Path** =
   `Dockerfile.cron`, **Cron Schedule** = `0 14 * * 1-5`.
3. Service → **Variables**: copy from `discovery-api` — `DATABASE_URL`,
   `ANTHROPIC_API_KEY`, `APIFY_API_KEY`, `ANTHROPIC_MODEL`, `DISCOVERY_JOBS_TITLES`,
   and optional `DISCOVERY_SIGNALBASE_*` / `DISCOVERY_JOBS_MAX_ROWS`.
4. **Deploy**. No HTTP port and no healthcheck — expected for a cron service.

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
- **Spend**: `GET /api/scoring/stats` → `month_discovery_cost` reflects measured
  per-company qualify tokens (real Anthropic usage, not flat estimates).

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
