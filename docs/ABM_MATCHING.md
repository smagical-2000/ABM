# ABM target-list cross-verification

Match companies surfaced by discovery against the sales team's uploaded ABM
target list (the Q2 accounts workbook). A discovered company that is **already a
named target account** *and* is showing a live buying signal is the
highest-value lead the pipeline can produce — this feature flags exactly those.

Deterministic and **zero-cost**: no LLM, no network. Parsing the workbook happens
once on upload; matching is O(1) dict lookups against an in-memory index.

## How a match is decided (strict)

| Tier | Rule | Why it's trustworthy |
|------|------|----------------------|
| **confirmed** | domain match | a domain belongs to exactly one organization |
| **confirmed** | normalized-name match **and** the company's signal location agrees on US state | rules out two different orgs that share a name in different states |
| **review** | normalized-name match with no state corroboration | surfaced, but flagged for human verification |

Names are normalized with the same `normalize_company_name()` used for discovery
dedup, so discovery and targets collapse identically. `(FKA ...)`/`(AKA ...)`
aliases in the target list are expanded, so a former name still matches.

### Why the "review" tier exists
Strict name+state matching correctly rejects e.g. *Parkview Health (Fort Wayne,
IN)* found in discovery against *Parkview Health System (CO)* on the list — but
it would also drop a legitimate match for a national system whose job posting is
in a different state than its HQ (Acadia, USPI). Rather than silently lose those,
they land in **review**: shown, but clearly lower-trust.

## Modules

- `auto_search/abm/parse.py` — parse the multi-sheet `.xlsx` into `TargetAccount`
  rows (detects the per-sheet name/website/state columns, skips non-list sheets).
- `auto_search/abm/matcher.py` — `AbmIndex.match(name, domain=, states=)` ->
  `AbmMatch | None`. Pure, no I/O.
- `auto_search/abm/util.py` — domain / US-state / alias helpers.
- `auto_search/abm/models.py` — `TargetAccount`, `AbmMatch`.

Persistence lives on the discovery repository: an `abm_targets` table in
Postgres, a sidecar `abm_targets.json` for the JSON store. The match index is
built into `app.state.abm_index` at startup and rebuilt on each upload.

## API

- `POST /api/abm/import` — body = raw `.xlsx` bytes; replaces the stored list.
  Returns `{stored, summary}`.
- `GET  /api/abm/summary` — `{total, by_segment, uploaded_at, indexed}`.
- `GET  /api/abm/matches` — panel companies on the target list (confirmed first).
- `GET  /api/panel?abm=confirmed` (or `abm=match`) — filter the panel to ABM hits.
- Every `PanelCompany` now carries `abm_match: AbmMatch | null`.

## UI (Discovery panel)

- An amber **"ABM target"** badge on matched rows ("ABM?" for review-tier), plus
  a callout in the detail drawer.
- An **"ABM list"** filter (All / On ABM list / Confirmed) and an "N on ABM list"
  count next to the company count.
- An **Upload ABM list** button that replaces the stored list from a `.xlsx`.

## Tests

`tests/test_abm_util.py`, `test_abm_matcher.py`, `test_abm_parse.py`,
`test_abm_api.py` cover the normalization/alias/domain/state helpers, the strict
tiering (including the Parkview false-positive guard), multi-sheet parsing, and
the import -> annotate -> filter API flow.
