#!/usr/bin/env python3
"""Env-manifest gate — every Railway service must carry its required env vars.

The class this kills (2026-07): a functionally-required var present on one
service but absent on a sibling is a SILENT no-op for weeks. REPLYIO_API_KEY
was absent on discovery-cron for 13 days (the Reply.io leg "cleanly no-opped"
while heat froze); N8N_CLAY_DISPATCH_URL/CLAY_BRIDGE_TOKEN lived only on the
web service, so Clay auto-dispatch would have no-opped forever on the cron
that actually captures leads.

The manifest (ops/env-manifest.json) is the source of truth for which vars
each service REQUIRES; this script diffs it against the live Railway config:

    python3 scripts/check_env_manifest.py            # all services, exit 1 on any missing
    python3 scripts/check_env_manifest.py --service discovery-cron

Read-only: it runs `railway variables --service X --json` and never sets or
prints a VALUE — the table names variables only (secret hygiene). A service
whose variables cannot be fetched FAILS the gate (no vacuous passes — the
same philosophy as ship.sh's refusal when neither web URL is set). "Extra"
vars (set live, required nowhere, not recognized as optional) are named but
never fail the gate — they are the zombie-config column (the
TRIGIFY_WEBHOOK_SECRET class: integration deleted, var lives on).

scripts/ship.sh runs this BEFORE the deploy loop and refuses to ship red.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

DEFAULT_MANIFEST = Path(__file__).resolve().parent.parent / "ops" / "env-manifest.json"

# BUILD_STAMP is set by ship.sh itself on every deploy; RAILWAY_* are injected
# by the platform. Neither is ever "missing config" nor a zombie.
IGNORED_EXACT = frozenset({"BUILD_STAMP"})
IGNORED_PREFIXES = ("RAILWAY_",)

_VAR_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")


class FetchError(RuntimeError):
    """railway variables could not be read for a service."""


def parse_manifest(raw: dict) -> tuple[dict[str, list[str]], set[str]]:
    """Split the manifest into {service: [required vars]} and the recognized-
    optional set. Top-level keys starting with "_" are notes, not services;
    list entries starting with "_" (the "_comment: ..." convention) are
    provenance notes. Malformed entries raise — a typo'd manifest must fail
    loudly, not silently weaken the gate."""
    services: dict[str, list[str]] = {}
    for key, val in raw.items():
        if key.startswith("_"):
            continue
        if not isinstance(val, list):
            raise ValueError(f"manifest[{key!r}] must be a list of var names")
        services[key] = _clean_entries(val, where=key)
    optional_raw = raw.get("_optional", [])
    if not isinstance(optional_raw, list):
        raise ValueError("manifest['_optional'] must be a list of var names")
    optional = set(_clean_entries(optional_raw, where="_optional"))
    return services, optional


def _clean_entries(entries: list, *, where: str) -> list[str]:
    out = []
    for entry in entries:
        if not isinstance(entry, str):
            raise ValueError(f"manifest[{where!r}] has a non-string entry: {entry!r}")
        if entry.startswith("_"):
            continue                     # "_comment: ..." provenance note
        if not _VAR_RE.match(entry):
            raise ValueError(f"manifest[{where!r}] has a malformed var name: {entry!r}")
        out.append(entry)
    return out


def live_var_names(vars_json: dict) -> set[str]:
    """The names that are genuinely SET. A var whose value is empty/whitespace
    is functionally missing — an empty API key satisfies nothing, so it must
    not satisfy the gate either."""
    return {k for k, v in vars_json.items() if str(v if v is not None else "").strip()}


def diff_service(required: list[str], live_names: set[str],
                 all_required: set[str], optional: set[str]) -> tuple[list[str], list[str]]:
    """The pure diff: (missing, extra) for one service.

    missing — required here but not set live (THE gate; any hit fails the run).
    extra   — set live but required by NO service and not recognized as
              optional/ignored: candidate zombie config, named but non-fatal.
    A var required on a sibling service is never extra here."""
    missing = sorted(set(required) - live_names)
    recognized = all_required | optional | IGNORED_EXACT
    extra = sorted(v for v in live_names
                   if v not in recognized and not v.startswith(IGNORED_PREFIXES))
    return missing, extra


def fetch_railway(service: str) -> dict:
    """Live variables for one service via the railway CLI (read-only)."""
    cmd = ["railway", "variables", "--service", service, "--json"]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except FileNotFoundError as e:
        raise FetchError("railway CLI not found on PATH") from e
    except subprocess.TimeoutExpired as e:
        raise FetchError(f"railway variables timed out for {service}") from e
    if p.returncode != 0:
        tail = (p.stderr or p.stdout or "").strip().splitlines()
        detail = tail[-1] if tail else "no output"
        raise FetchError(f"railway variables failed for {service}: {detail}")
    try:
        data = json.loads(p.stdout)
    except ValueError as e:
        raise FetchError(f"railway variables returned non-JSON for {service}") from e
    if not isinstance(data, dict):
        raise FetchError(f"unexpected variables payload for {service}")
    return data


def _cell(names: list[str]) -> str:
    return ", ".join(names) if names else "-"


def run_check(raw_manifest: dict, *, fetch=fetch_railway,
              only: list[str] | None = None) -> int:
    """Diff every service against the manifest and print the table.
    Returns 1 if ANY required var is missing anywhere (or a service could not
    be read — a gate that cannot see is a gate that must not pass), else 0."""
    services, optional = parse_manifest(raw_manifest)
    if only:
        unknown = sorted(set(only) - set(services))
        if unknown:
            print(f"unknown service(s): {', '.join(unknown)} "
                  f"(manifest has: {', '.join(sorted(services))})", file=sys.stderr)
            return 1
        services = {s: services[s] for s in only}
    all_required = {v for req in services.values() for v in req}

    rows: list[tuple[str, str, str]] = []
    n_missing = 0
    fetch_failed = False
    for svc, required in services.items():
        try:
            live = live_var_names(fetch(svc))
        except FetchError as e:
            rows.append((svc, f"UNREADABLE ({e})", "-"))
            fetch_failed = True
            continue
        missing, extra = diff_service(required, live, all_required, optional)
        n_missing += len(missing)
        rows.append((svc, _cell(missing), _cell(extra)))

    headers = ("service", "missing (required, not set)", "extra (set, required nowhere)")
    widths = [max(len(headers[i]), *(len(r[i]) for r in rows)) for i in range(3)]
    line = "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers))
    print(line)
    print("  ".join("-" * w for w in widths))
    for r in rows:
        print("  ".join(r[i].ljust(widths[i]) for i in range(3)))

    if fetch_failed:
        print("\nFAIL: could not read variables for at least one service — "
              "refusing to pass vacuously.")
        return 1
    if n_missing:
        print(f"\nFAIL: {n_missing} required var(s) missing. Set them "
              "(railway variables --set VAR=... --service <svc>) or, if the "
              "requirement truly changed, update ops/env-manifest.json.")
        return 1
    print("\nOK: every service has all its required vars.")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Diff live Railway env vars against "
                                             "ops/env-manifest.json (read-only)")
    ap.add_argument("--manifest", default=str(DEFAULT_MANIFEST),
                    help="path to the manifest (default: ops/env-manifest.json)")
    ap.add_argument("--service", action="append", default=None, metavar="NAME",
                    help="check only this service (repeatable; default: all)")
    args = ap.parse_args(argv)
    raw = json.loads(Path(args.manifest).read_text())
    return run_check(raw, only=args.service)


if __name__ == "__main__":
    sys.exit(main())
