"""CSV import — a Definitive Healthcare export becomes scoreable accounts.

Pure parsing, no I/O beyond the text it's handed. Detects which Definitive
schema a file is (Health Systems vs Physician Groups), maps the columns that
pre-fill the rubric into an Account's known facts, and reports the mapping so
the import wizard can show it before committing.

Schema is matched by header name with fallbacks, so a slightly different export
still imports; unmatched columns are simply not carried.
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


def parse_csv(text: str) -> ImportResult:
    """Parse a Definitive export into scoreable accounts + a mapping summary."""
    reader = csv.DictReader(io.StringIO(text))
    headers = [h.strip() for h in (reader.fieldnames or [])]
    schema = detect_schema(headers)
    if schema is None:
        raise ImportError_(
            "Unrecognized CSV. Expected a Definitive Healthcare Health Systems "
            "or Physician Groups export."
        )

    framework = framework_for_segment(schema.segment).key
    fact_label = dict(schema.fact_cols)
    accounts: list[Account] = []
    seen_ids: set[str] = set()
    skipped = 0

    for raw in reader:
        row = {(k or "").strip(): (v or "").strip() for k, v in raw.items()}
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
