# evals/ — the regression discipline

A bug is not "done" when the symptom disappears. It is done when a test makes it impossible to
reintroduce silently. This folder is how we hold that line.

## What's here

- **`bugs.json`** — the registry. One entry per notable bug we have shipped and fixed, each with
  the test that now guards it (`guard`) and the generalizable rule (`lesson`). Read it on a cold
  start: it is the fastest way to learn this codebase's real gotchas (in-browser JSX, cron/panel
  parity, id schemes, state TTLs).

## How it works (and how it relates to CI)

Our bugs are code bugs, so each one's **guard is a real `pytest` test** that already runs in CI
(`.github/workflows/ci.yml`). `bugs.json` is the **human- and agent-readable index** mapping
bug -> guard -> lesson. It is not a separate runner; CI is the gate. The index exists so that:

1. A fresh agent (or a new teammate) learns the landmines before stepping on them.
2. When a bug recurs, you find the existing guard instead of writing a duplicate.
3. We can audit that every shipped bug actually grew a test.

## The rule (do this every time)

When you fix a bug:
1. Write or extend the test that fails on the bug and passes on the fix.
2. Add an entry to `bugs.json` (see `_schema` in that file): id, date, area, symptom,
   **root_cause** (not the symptom), `guard` (the `file::test_name`), and the `lesson`.
3. Confirm the guard is in the CI path (`pytest -q` green).

Prune an entry only after its guard has held for 6+ months and the area was rewritten — a stable,
well-covered area does not need its old scars catalogued forever.
