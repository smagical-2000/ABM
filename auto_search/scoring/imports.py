"""CSV import — a Definitive Healthcare export becomes scoreable accounts.

Pure parsing, no I/O beyond the text it's handed. Detects which Definitive
schema a file is (Health Systems vs Physician Groups), maps the columns that
pre-fill the rubric into an Account's known facts, and reports the mapping so
the import wizard can show it before committing.

Schema is matched by header name with fallbacks, so a slightly different export
still imports; unmatched columns are simply not carried.

A third shape is accepted when neither Definitive schema matches: a GENERIC
accounts list (e.g. an SFDC or analysis export) with just a name column and a
domain column (2026-07-08, for the SAO-analysis cohort). Generic rows carry NO
segment — the import endpoint classifies each row (name+domain -> health
system / specialty / payer, high confidence only) and drops the rest, so a
mixed list is never scored on a guessed rubric.
"""

from __future__ import annotations

import csv
import io
import logging
from dataclasses import dataclass, field

from auto_search.normalize import clean_domain, slugify
from auto_search.scoring.frameworks import framework_for_segment
from auto_search.scoring.models import Account

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Schema:
    key: str
    label: str
    segment: str
    name_col: str
    detect: tuple[str, ...]                 # columns whose presence identifies it
    fact_cols: tuple[tuple[str, str], ...]  # (csv column, known-fact label)
    domain_col: str | None = None


_SCHEMAS: tuple[Schema, ...] = (
    Schema(
        key="health_systems",
        label="Health Systems",
        segment="health_system",
        name_col="Hospital Name",
        detect=("Net Patient Revenue",),
        fact_cols=(
            ("Net Patient Revenue", "Net Patient Revenue"),
            ("Electronic Health/Medical Record - Inpatient", "EHR Inpatient"),
            ("Electronic Health/Medical Record - Ambulatory", "EHR Ambulatory"),
            ("Epic?", "Epic"),
            ("Revenue Cycle Management", "RCM Vendor"),
            ("# of Staffed Beds", "Staffed Beds"),
            ("IDN Parent", "IDN Parent"),
            ("State", "State"),
        ),
    ),
    Schema(
        key="physician_groups",
        label="Physician Groups",
        segment="specialty",
        name_col="Physician Group Name",
        detect=("# of Physicians",),
        domain_col="Website",
        fact_cols=(
            ("# of Physicians", "Physicians"),
            ("Number of Locations", "Locations"),
            ("Ambulatory EMR", "Ambulatory EMR"),
            ("Main Specialty", "Specialty"),
            ("ICP Specialty", "ICP Specialty"),
            ("Medicare Allowed Amt", "Medicare Allowed"),
            ("City", "City"),
            ("State", "State"),
        ),
    ),
)


@dataclass
class MappedColumn:
    col: str
    fact: str | None       # known-fact label, or None when it maps to a field


@dataclass
class ImportResult:
    schema_key: str
    schema_label: str
    segment: str
    accounts: list[Account]
    mapping: list[MappedColumn]
    rows_total: int
    skipped: int = 0
    unmatched_columns: list[str] = field(default_factory=list)


class ImportError_(ValueError):
    """Raised when a CSV can't be matched to a known schema."""


def detect_schema(headers: list[str]) -> Schema | None:
    hset = {h.strip() for h in headers}
    for schema in _SCHEMAS:
        if all(col in hset for col in schema.detect):
            return schema
    return None


# ── generic accounts list ─────────────────────────────────────────────
# Checked only AFTER the Definitive schemas fail, so a DHC export can never be
# mistaken for it. First matching name/domain header wins.
GENERIC_KEY = "generic_accounts"
GENERIC_LABEL = "Accounts list (name + domain)"
GENERIC_SEGMENT = "mixed"                     # per-row segments come from the classifier
# Every generic row costs a classification call (preview AND commit), inside one
# synchronous request — cap the file size so a huge list can't time the wizard
# out or rack up unmetered LLM spend (QA F4).
GENERIC_MAX_ROWS = 500
_GENERIC_NAME_COLS = ("Account Name", "Company Name", "Account", "Company", "Name")
_GENERIC_DOMAIN_COLS = ("Website Domain", "Company Domain", "Domain", "Website", "URL")


def detect_generic(headers: list[str]) -> tuple[str, str] | None:
    """(name_col, domain_col) when the file is a plain accounts list, else None."""
    hset = {h.strip() for h in headers}
    name_col = next((c for c in _GENERIC_NAME_COLS if c in hset), None)
    domain_col = next((c for c in _GENERIC_DOMAIN_COLS if c in hset), None)
    return (name_col, domain_col) if name_col and domain_col else None


def _parse_generic(reader: csv.DictReader, headers: list[str],
                   name_col: str, domain_col: str) -> ImportResult:
    """Plain name+domain rows -> segment-less accounts (the endpoint classifies).
    When the chosen domain cell is blank, any other recognized domain-ish column
    is tried, so 'Website Domain' empty + 'Website' filled still yields a domain.
    Rows with a name but NO domain are kept — the classifier and scorer both
    work from the name alone (same as an AE lookup)."""
    fallbacks = [c for c in _GENERIC_DOMAIN_COLS if c != domain_col and c in headers]
    accounts: list[Account] = []
    seen_ids: set[str] = set()
    skipped = 0
    for row_no, raw in enumerate(reader, start=1):
        if row_no > GENERIC_MAX_ROWS:
            raise ImportError_(
                f"Accounts lists are capped at {GENERIC_MAX_ROWS} rows per import "
                "(every row is individually classified before it can be scored). "
                "Split the file and import in parts.")
        # `if k is not None` drops DictReader's restkey: a ragged row (extra
        # comma in a hand-edited CSV) parks overflow cells in a LIST under key
        # None, and .strip() on that list 500'd the whole import (QA F1).
        row = {(k or "").strip(): (v or "").strip()
               for k, v in raw.items() if k is not None}
        name = row.get(name_col, "")
        if not name:
            skipped += 1
            continue
        account_id = "csv_" + slugify(name)
        if account_id in seen_ids:
            skipped += 1
            continue
        seen_ids.add(account_id)
        domain = clean_domain(_strip_url(row.get(domain_col, "")))
        for alt in fallbacks:
            if domain:
                break
            domain = clean_domain(_strip_url(row.get(alt, "")))
        accounts.append(Account(
            account_id=account_id, name=name,
            segment="", framework="",           # assigned per row by the classifier
            source="csv", domain=domain, firmographics={},
        ))
    mapping = [MappedColumn(col=name_col, fact=None),
               MappedColumn(col=domain_col, fact="Domain")]
    unmatched = [h for h in headers if h not in (name_col, domain_col)]
    logger.info("csv import: generic accounts list, %d accounts (%d skipped)",
                len(accounts), skipped)
    return ImportResult(
        schema_key=GENERIC_KEY, schema_label=GENERIC_LABEL, segment=GENERIC_SEGMENT,
        accounts=accounts, mapping=mapping, rows_total=len(accounts) + skipped,
        skipped=skipped, unmatched_columns=unmatched,
    )


def parse_csv(text: str) -> ImportResult:
    """Parse a Definitive export into scoreable accounts + a mapping summary."""
    reader = csv.DictReader(io.StringIO(text))
    headers = [h.strip() for h in (reader.fieldnames or [])]
    schema = detect_schema(headers)
    if schema is None:
        generic = detect_generic(headers)
        if generic is None:
            raise ImportError_(
                "Unrecognized CSV. Expected a Definitive Healthcare Health Systems "
                "or Physician Groups export, or a plain accounts list with a name "
                "column (e.g. 'Account Name') and a domain column (e.g. 'Website "
                "Domain')."
            )
        return _parse_generic(reader, headers, *generic)

    framework = framework_for_segment(schema.segment).key
    fact_label = dict(schema.fact_cols)
    accounts: list[Account] = []
    seen_ids: set[str] = set()
    skipped = 0

    for raw in reader:
        # `if k is not None` drops DictReader's restkey: a ragged row (extra
        # comma in a hand-edited CSV) parks overflow cells in a LIST under key
        # None, and .strip() on that list 500'd the whole import (QA F1).
        row = {(k or "").strip(): (v or "").strip()
               for k, v in raw.items() if k is not None}
        name = row.get(schema.name_col, "")
        if not name:
            skipped += 1
            continue

        account_id = "csv_" + slugify(name)
        if account_id in seen_ids:
            skipped += 1
            continue
        seen_ids.add(account_id)

        facts = {
            label: row[col]
            for col, label in schema.fact_cols
            if row.get(col)
        }
        domain = clean_domain(_strip_url(row.get(schema.domain_col or "", "")))

        accounts.append(Account(
            account_id=account_id,
            name=name,
            segment=schema.segment,
            framework=framework,
            source="csv",
            domain=domain,
            firmographics=facts,
        ))

    mapped_cols = {schema.name_col, *(c for c, _ in schema.fact_cols)}
    if schema.domain_col:
        mapped_cols.add(schema.domain_col)
    mapping = [MappedColumn(col=schema.name_col, fact=None)]
    mapping += [MappedColumn(col=c, fact=fact_label[c]) for c, _ in schema.fact_cols]
    unmatched = [h for h in headers if h not in mapped_cols]

    logger.info("csv import: %s schema, %d accounts (%d skipped)",
                schema.key, len(accounts), skipped)
    return ImportResult(
        schema_key=schema.key, schema_label=schema.label, segment=schema.segment,
        accounts=accounts, mapping=mapping, rows_total=len(accounts) + skipped,
        skipped=skipped, unmatched_columns=unmatched,
    )


def _strip_url(value: str) -> str | None:
    """Reduce a website cell to a bare domain candidate for clean_domain."""
    v = (value or "").strip().lower()
    if not v:
        return None
    v = v.replace("https://", "").replace("http://", "")
    v = v.split("/")[0]
    if v.startswith("www."):
        v = v[4:]
    return v or None
