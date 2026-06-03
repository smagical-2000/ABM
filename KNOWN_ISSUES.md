# Known issues

Operational caveats and deferred work for the Discovery pipeline. Keep this
honest — it's the first place to look when something behaves oddly.

## Claude / Anthropic rate limit throttles large qualification runs

**Symptom:** during a discovery run, the log shows repeated
`RateLimitError (429) ... rate limit of 30,000 input tokens per minute` and some
companies finish as `error` instead of qualified/disqualified.

**Cause:** the company/ICP qualifier (`auto_search/qualifier.py`) uses Claude
with the `web_search` tool. Each qualification re-reads its search results on
every turn, so a single call can be ~15–25k input tokens; back-to-back
qualifications exceed the org's **30k input-tokens/minute** tier limit.

**Impact:** not data loss — `error` is intentionally *not* a "decided" status,
so those companies are retried on the next run (see `_DECIDED_STATUSES`). It
just slows throughput and wastes some scraped rows.

**Mitigations (in order of effort):**
1. Trim `_WEB_SEARCH_MAX_USES` in `qualifier.py` (6 → 4): ~30% fewer input
   tokens per qualification, small quality cost.
2. Pace qualifications — add a short sleep / token-bucket between companies in
   `pipeline.run` so sustained usage stays under 30k/min.
3. Raise the Anthropic usage tier (purchase credits) — the real fix for volume.
4. Lower `--limit` per run (fewer companies/run) as a stopgap.

## Defer hides a company with no way back (tracked separately)

Clicking **Defer** sets `review_status='deferred'`, which removes the company
from the panel (it only lists `pending`). There is currently no UI to view or
restore deferred companies. Spun off as its own task — add a Deferred
view + Restore-to-queue action.

## Live activity feed shows outcomes, not in-progress

The panel's live feed streams *decided* companies (qualified/disqualified) as
they're saved. It does not yet show "qualifying X…" mid-flight — that needs the
runner to report the company currently being qualified (e.g. a `current` field
on `connector_runs`). The pulsing banner covers the scraping phase meanwhile.

## CDN React build + in-browser Babel

`web/discovery` loads React + Babel from a CDN and transpiles JSX in the
browser. Fine for this internal tool; for a public/production UI, move to a real
build step (Vite) so there's no per-load transpile and no CDN dependency.
