# QA checklist — sign-off gate

**Do not merge `feat/auto-search-layoffs` to `main` until every step below passes
and the reviewer signs off at the bottom.** This is a manual pass for a second
person on the deployed Railway URL (web service), plus one local discovery check.

Tester: ______________________   Date: ____________   Build/commit: ____________

| # | Check | How | Pass when |
|---|-------|-----|-----------|
| 1 | **Auth gate** | Open the URL in a fresh/incognito window | Browser prompts for Basic auth; wrong creds rejected; correct creds load the app. Hitting `/api/scored` with no auth returns 401. |
| 2 | **Logo** | Look at the header + browser tab | The Magical logo shows in the header next to the wordmark, and as the favicon (placeholder is fine). |
| 3 | **CSV import** | Scored tab → Import accounts → drop a Definitive CSV | Schema detected, mapping shown, accounts land as **queued** (not auto-scored), tagged with the filename (Import filter). |
| 4 | **Re-import skip** | Import the **same** CSV again | Already-known rows are skipped; only genuinely new rows are added (no duplicates). |
| 5 | **Score one** | Open a queued account → Score now (or Score N in a batch) | It moves to scoring → resolves to a fit + pillars within ~1-2 min; no row stuck "scoring" > 5 min. |
| 6 | **Estimated chips** | Open a scored account drawer | Inferred facts show an **Estimated** chip, unconfirmed show **Unconfirmed**, with tooltips. The CSV firmographics are not flagged. |
| 7 | **Budget guard** | Check the cost meter; try "Score all" beyond budget | Meter shows month spend vs $200. A batch that would exceed budget is capped server-side (not just a UI warning). |
| 8 | **Export** | Set the Import filter to your file → Export (and try row-select Export) | A CSV downloads with only those rows, including Analyst Total / Official Total / QA columns. |
| 9 | **Promote** | Discovery tab → Promote a qualified company | It leaves the panel and appears in Scored (as `acc_…`); discovery count + nav badge stay correct across tabs. |
| 10 | **Discovery dry run** (local) | `DATABASE_URL=… python scripts/run_discovery.py --days 1 --no-limit --no-qualify` | Pulls + dedups across all sources, prints "would qualify" rows, spends no Claude, one connector failing does not kill the others. |

## Reset note
To re-run a clean cost measurement, use **Reset** in the Scored tab (clears
scores back to queued, non-destructive). It must require the confirm step.

## Sign-off
- [ ] All 10 steps pass
- [ ] No P0 from the production-hardening batch is open
- Reviewer signature: ______________________

> Only after this is checked may the owner merge to `main`.
