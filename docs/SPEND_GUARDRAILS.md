# Spend guardrails

Four distinct mechanisms protect against runaway LLM spend. They stack — the
monthly budget gates the start; the rest catch what it can't.

| Mechanism | Scope | When it fires | What happens | Tunable |
|-----------|-------|---------------|--------------|---------|
| **Monthly budget exceeded** | The whole month | A paid op would push month-to-date past `SCORING_MONTHLY_BUDGET` ($200) | Start is refused (429 / `budget_blocked`); a batch is capped to what fits | `SCORING_MONTHLY_BUDGET` |
| **confirm_large_spend** | One operation, pre-flight | A batch's *estimate* exceeds `SPEND_MAX_OP_ESTIMATE_USD` ($150) | API returns 400 with the estimate; caller must re-send `confirm_large_spend: true` (the Scored UI shows a confirm) | `SPEND_MAX_OP_ESTIMATE_USD` |
| **Overheated** | One operation, mid-flight | Actual spend passes `estimate × SPEND_OP_OVERRUN_RATIO` (1.4×) or `SPEND_OP_HARD_CAP_USD` ($200) | Batch stops scheduling NEW accounts (in-flight finish); op marked `overheated` | `SPEND_OP_OVERRUN_RATIO`, `SPEND_OP_HARD_CAP_USD` |
| **Per-account spike** | One account | A single account's cumulative cost passes `SPEND_MAX_PER_ACCOUNT_USD` ($10; normal ~$0.10) | That account drops to `error` ("overheat: per-account spend cap"), no more LLM on it; **the batch keeps going** | `SPEND_MAX_PER_ACCOUNT_USD` |
| **Estimated chips** (not a guard) | UI | A dimension was inferred, not confirmed | Shows an "Estimated"/"Unconfirmed" chip so a soft number isn't read as fact | — |

## How spend is recorded

Every paid step writes a `cost_event` (`score | qa | dossier | qualify`) tied to
a `spend_operation`. So spend is auditable, not invisible:

- `score_batch` / `score_one` / `promote` / `dossier` — scoring spend.
- `discovery_cron` — discovery qualify spend (estimate-based;
  `DISCOVERY_EST_QUAL_COST`, default $0.12/company).

`GET /api/scoring/stats` exposes the rollup: `month_scoring_cost`,
`month_discovery_cost`, `month_total_cost`, `daily_total_cost`, and the recent
`last_operations`.

## Daily soft watch

`SPEND_DAILY_WARN_USD` ($50) flags the day amber in stats (`daily_over_warn`).
`SPEND_DAILY_CAP_USD` (0 = off) is an optional hard daily ceiling.

## What is NOT guarded

- **CSV import is free** and never blocked on estimate — it only queues rows.
- The monthly `budget.assert_affordable` is always called first; these guards
  add to it, they do not replace it.
