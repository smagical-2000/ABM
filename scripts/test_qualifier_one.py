"""Test the qualifier on hardcoded companies — bypass the connector.

Useful for:
    - Seeing the qualifier logging work end-to-end
    - Tuning the ICP prompt without depending on Apify data
    - Verifying a specific company's verdict before bulk runs

Run:
    python scripts/test_qualifier_one.py              # all cases
    python scripts/test_qualifier_one.py orthoindy    # one case
    python scripts/test_qualifier_one.py --custom "Acme Health"

Each run shows:
    • Logger output: "qualifying X via web_search..."
    • Web search queries Claude executed
    • Verdict icon + segment + confidence + evidence URL
    • Reasoning text from Claude
    • Trace file path written to data/qualifier_traces/
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from auto_search.models import RawSignal
from auto_search.qualifier import qualify

load_dotenv()


# Hand-picked test cases covering each ICP class
TEST_CASES = {
    "orthoindy": {
        "expected": "qualify as specialty / ortho",
        "company": "OrthoIndy",
        "industry": "Healthcare",
        "country": "United States",
        "laid_off": 50,
    },
    "labcorp": {
        "expected": "disqualify — lab testing only",
        "company": "LabCorp",
        "industry": "Healthcare",
        "country": "United States",
        "laid_off": 200,
    },
    "mayo": {
        "expected": "disqualify — mega health system",
        "company": "Mayo Clinic",
        "industry": "Healthcare",
        "country": "United States",
        "laid_off": 100,
    },
    "twilio": {
        "expected": "disqualify — pure tech / SaaS",
        "company": "Twilio",
        "industry": "Communications",
        "country": "United States",
        "laid_off": 300,
    },
    "centene": {
        "expected": "qualify as payer / medicaid_mco",
        "company": "Centene",
        "industry": "Insurance",
        "country": "United States",
        "laid_off": 500,
    },
}


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="  %(asctime)s [%(levelname)-5s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("anthropic").setLevel(logging.WARNING)


def make_signal(case: dict) -> RawSignal:
    return RawSignal(
        source="manual_test",
        source_external_id=f"test::{case['company'].lower()}",
        signal_type="layoff",
        company_name_raw=case["company"],
        observed_at=datetime.now(UTC),
        signal_strength=0.7,
        payload={
            "laid_off_count": case["laid_off"],
            "industry_raw":   case["industry"],
            "country":        case["country"],
            "location_hq":    case.get("location") or "USA",
        },
    )


async def run_one(name: str, case: dict) -> None:
    print(f"\n{'═' * 72}")
    print(f"  TEST: {name}   (expected: {case['expected']})")
    print(f"{'═' * 72}\n")

    signal = make_signal(case)
    result = await qualify(signal)

    print()
    print("  RESULT:")
    print(f"    qualified:     {result.qualified}")
    print(f"    segment:       {result.segment}")
    print(f"    sub_segment:   {result.sub_segment}")
    print(f"    company_type:  {result.company_type}")
    print(f"    employees:     {result.approximate_employees}")
    print(f"    confidence:    {result.confidence}")
    print(f"    needs_review:  {result.needs_human_review}")
    print(f"    evidence_url:  {result.evidence_url}")
    print(f"    reasoning:     {result.reasoning}")


async def main(args: argparse.Namespace) -> None:
    configure_logging()

    if args.custom:
        case = {
            "expected": "(custom — no expectation)",
            "company": args.custom,
            "industry": "Healthcare",
            "country": "United States",
            "laid_off": 50,
        }
        await run_one("custom", case)
        return

    if args.case:
        if args.case not in TEST_CASES:
            print(f"Unknown case {args.case!r}. Available: {list(TEST_CASES)}")
            sys.exit(1)
        await run_one(args.case, TEST_CASES[args.case])
        return

    for name, case in TEST_CASES.items():
        await run_one(name, case)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("case", nargs="?",
                   help="Run one test case by name "
                        "(orthoindy / labcorp / mayo / twilio / centene). "
                        "Default: run all.")
    p.add_argument("--custom", help="Custom company name (overrides preset cases)")
    asyncio.run(main(p.parse_args()))
