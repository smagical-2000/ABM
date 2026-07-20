# Deduplication

How the two systems avoid scoring or qualifying the same thing twice, and the
known gaps we are deliberately not closing in v1.

## Discovery (System A)

Three layers, cheapest first:

1. **Signal-level (DB constraint).** A signal row is unique on
   `(source, source_external_id)` in `auto_search/db/schema.sql`. The same
   posting / press item pulled again on the next run is a no-op insert, so a
   connector can be re-run safely.

2. **Company grouping (in-run).** `auto_search/normalize.py:normalize_company_name`
   maps raw names ("Acme Health, Inc." / "ACME HEALTH") to a single
   `company_key`. `pipeline.collect_unique_companies` groups a run's signals by
   that key so the qualifier sees each company **once per run**, not once per
   signal. The companies table has a UNIQUE constraint on the key.

3. **Already-decided skip (cross-run).** `repo.already_qualified` lets
   `pipeline.run(skip_already_qualified=...)` drop companies that already have a
   decision, so the daily cron never re-pays Claude to re-qualify a company it
   has seen. New signals still attach to the existing company row.

Net effect: re-running discovery is idempotent on cost — only genuinely new
companies reach the (paid) qualifier.

## Scoring (System B)

- **CSV import.** `account_id = csv_{slugify(name)}`. On import,
  `service.exists(account_id)` skips rows already present, so re-importing the
  same file (or an overlapping one) adds only new accounts. Each import is also
  tagged with an `import_label` (filename + time) for filtering/export.
- **Promote.** A promoted discovery company becomes `acc_{company_key}` — the
  same normalized key as discovery, so promoting twice upserts the one account
  rather than creating duplicates.

## Known gaps (intentionally deferred)

- **Name vs domain duplicates.** Two records for the same organization that
  differ only by domain (e.g. a CSV "West Virginia University Dept of Medicine"
  vs a promoted "WVU Medicine") will NOT merge — the keys differ. We do not
  build domain-based merge in v1; it needs a canonical-entity/accounts table
  (see DB_REVIEW.md). For now, the Import filter + the source column make such
  pairs visible.
- **Cross-source company aliasing.** If two connectors name the same company
  differently in ways `normalize_company_name` does not catch, they group
  separately. Rare; surfaced in the panel, not auto-merged.
- **No fuzzy matching.** Matching is exact-on-normalized-key, not fuzzy. This is
  a deliberate v1 choice (predictable, cheap, no false merges).
