# Release & QA Protocol

> The fixed way we ship. Follows the MyZone "Agentic Development Best Practices"
> field guide (May 2026), right-sized for this repo. AGENTS.md links here; this
> doc is the source of truth for versioning, release flow, and QA gates.

## Versioning — semver git tags

Releases are annotated git tags on `main`: `vMAJOR.MINOR.PATCH`.

| Bump | When |
|---|---|
| MAJOR | Breaking change to data model, API contracts, or a workflow the GTM team relies on |
| MINOR | New feature or connector (a new tab, a new signal source, a new rule) |
| PATCH | Bug fix, copy change, cost tuning, doc-only |

- `BUILD_STAMP` on a release deploy IS the tag (e.g. `v1.2.0`), so the running
  version is provable from the logs (`[run_daily] rev … build v1.2.0`).
- Interim/hotfix deploys keep descriptive stamps (`fix-social-loc-…`) until the
  next release train picks them up.
- Each tag gets a GitHub Release whose notes are the change-log cards shipped
  since the previous tag (same wording as the Slack/Notion change log).

## The release loop (per feature)

1. **Branch per feature** — `feat/<thing>` or `fix/<thing>` off `main`. One
   feature, one small PR. (AGENTS.md "How we build here" governs the build itself.)
2. **Inner verify loop before "done"** — ruff, pytest (with a new test for the
   change), JSX transpile check, `tests/ui` Playwright suite when UI changed.
3. **PR → CI green → review → merge to `main`.**
4. **Tag + deploy**: when deploying, tag `main`, deploy from a clean checkout of
   that tag with `BUILD_STAMP=<tag>`, per service (`railway up -s <svc>`).
5. **Verify on prod** — the stamp must appear in the service's logs (deploy
   SUCCESS status is never proof), plus a feature-appropriate smoke check.
6. **Change log** — card drafted, Sunny approves, posts to Slack + Notion.
   A change auto-logs only at ship-to-prod-VERIFIED, not at deploy.
7. **Every bug found → `evals/bugs.json`** with the test that now guards it.

## Hypercare exception (honest escape hatch)

During incident response, deploying a fix from the working tree BEFORE the PR
is allowed. The debt rules:
- The commit must be pushed to GitHub **the same day** (drift budget: 24h).
- It enters the next release train and gets tagged like everything else.
- No exception for the verify-on-prod step — stamps always.

## QA gates, right-sized (MyZone guide ch. 8/10/13)

| Layer | Gate | When |
|---|---|---|
| Lint + types of mistakes linters catch | `ruff check` | Every change |
| Unit / functional | `pytest -q`; new logic ships with tests | Every change |
| Regression (agent + code) | `evals/bugs.json` entry + guarding test | Every shipped bug, no exceptions |
| Browser / e2e | `tests/ui/test_ui_smoke.py` (Playwright vs seeded local Postgres) — extend it with every user-visible feature | Any UI change |
| Fresh-context review | Sub-agent code review + security pass on auth/secrets/spend paths | Connectors, money paths, auth |
| Deploy QA | BUILD_STAMP visible in prod logs + smoke of the changed behavior | Every deploy |
| Ops monitoring | Failure alerts + silence watchdog (ops/alerts.py, ops/watchdog.py) | Continuous |

## Release train

Weekly (or after any hypercare burst): merge accumulated PRs to `main`, run the
full suite, tag, deploy from the tag, post the release's change-log cards.
This is what keeps GitHub, prod, and the change log telling the same story.

## Bootstrap plan (2026-07-08)

1. Merge PR #30 (`release/2026-07-08-hypercare`) — carries prod's current state.
2. Close PRs #27/#28/#29 as superseded (their commits are contained in #30).
3. Tag `v1.0.0` = that merge commit (prod as of the hypercare batch).
4. From then on: the loop above, no unversioned deploys outside hypercare.
