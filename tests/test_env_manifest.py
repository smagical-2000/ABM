"""Env-manifest gate — the diff logic, with NO railway calls (fetch is injected).

The class this gate kills: a functionally-required var present on one service and
absent on its sibling is a SILENT no-op for weeks (REPLYIO_API_KEY missing on
discovery-cron = 13 days of frozen Reply.io heat; the Clay bridge vars missing on
linkedin-tofu-cron = auto-dispatch would have no-opped forever). The checker diffs
each service's live Railway variables against ops/env-manifest.json and refuses to
ship on ANY missing required var.
"""

import importlib.util
import json
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_SPEC = importlib.util.spec_from_file_location(
    "check_env_manifest", _ROOT / "scripts" / "check_env_manifest.py")
cem = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(cem)


_MANIFEST = {
    "_comment": "top-level notes are ignored",
    "_optional": ["KNOB_WITH_DEFAULT", "FEATURE_FLAG", "_comment: knobs never required"],
    "svc-a": [
        "_comment: core (entrypoint -> db/*)",
        "DATABASE_URL",
        "SOME_API_KEY",
        "_comment: webhooks",
        "SLACK_HOOK",
    ],
    "svc-b": ["DATABASE_URL", "BRIDGE_TOKEN"],
}


# ── parse_manifest ───────────────────────────────────────────────────────────

def test_parse_manifest_filters_comment_keys_and_entries():
    services, optional = cem.parse_manifest(_MANIFEST)
    assert set(services) == {"svc-a", "svc-b"}            # "_"-prefixed keys are not services
    assert services["svc-a"] == ["DATABASE_URL", "SOME_API_KEY", "SLACK_HOOK"]
    assert services["svc-b"] == ["DATABASE_URL", "BRIDGE_TOKEN"]
    assert optional == {"KNOB_WITH_DEFAULT", "FEATURE_FLAG"}  # comment entries dropped


def test_parse_manifest_rejects_malformed_entries():
    """A typo'd manifest must fail loudly, not silently weaken the gate."""
    import pytest
    with pytest.raises(ValueError):
        cem.parse_manifest({"svc": ["lowercase_bad"]})
    with pytest.raises(ValueError):
        cem.parse_manifest({"svc": "NOT_A_LIST"})


# ── live_var_names ───────────────────────────────────────────────────────────

def test_live_var_names_treats_empty_values_as_unset():
    """A var set to '' is functionally missing — an empty API key satisfies
    nothing, so it must not satisfy the gate either."""
    live = cem.live_var_names({"A": "x", "B": "", "C": "   ", "D": None, "E": "0"})
    assert live == {"A", "E"}


# ── diff_service ─────────────────────────────────────────────────────────────

def _diff(required, live, *, all_required=None, optional=frozenset()):
    all_req = set(all_required) if all_required is not None else set(required)
    return cem.diff_service(required, set(live), all_req, set(optional))


def test_missing_required_var_is_reported():
    missing, extra = _diff(["DATABASE_URL", "REPLYIO_API_KEY"], {"DATABASE_URL"})
    assert missing == ["REPLYIO_API_KEY"]
    assert extra == []


def test_fully_configured_service_is_clean():
    missing, extra = _diff(["DATABASE_URL", "SLACK_HOOK"], {"DATABASE_URL", "SLACK_HOOK"})
    assert missing == []
    assert extra == []


def test_extra_flags_vars_required_nowhere():
    """A live var no service requires and nothing recognizes = a zombie
    (the TRIGIFY_WEBHOOK_SECRET class: the integration was removed, the var
    lives on). It never fails the gate, it just gets named."""
    missing, extra = _diff(["DATABASE_URL"], {"DATABASE_URL", "TRIGIFY_WEBHOOK_SECRET"})
    assert missing == []
    assert extra == ["TRIGIFY_WEBHOOK_SECRET"]


def test_var_required_on_sibling_service_is_not_extra():
    """CLAY_BRIDGE_TOKEN is required on tofu-cron; finding it also set on the
    web service must not flag it — cross-service presence is normal."""
    missing, extra = _diff(
        ["DATABASE_URL"], {"DATABASE_URL", "CLAY_BRIDGE_TOKEN"},
        all_required={"DATABASE_URL", "CLAY_BRIDGE_TOKEN"})
    assert (missing, extra) == ([], [])


def test_optional_and_ignored_vars_are_not_extra():
    live = {"DATABASE_URL", "KNOB_WITH_DEFAULT", "BUILD_STAMP",
            "RAILWAY_SERVICE_ID", "RAILWAY_PUBLIC_DOMAIN"}
    missing, extra = _diff(["DATABASE_URL"], live, optional={"KNOB_WITH_DEFAULT"})
    assert (missing, extra) == ([], [])


# ── run_check (aggregation + exit code; fetch injected, no railway) ──────────

def _fake_fetch(live_by_svc):
    def fetch(service):
        return live_by_svc[service]
    return fetch


def test_run_check_fails_on_any_missing_var(capsys):
    live = {
        "svc-a": {"DATABASE_URL": "x", "SOME_API_KEY": "x", "SLACK_HOOK": "x"},
        "svc-b": {"DATABASE_URL": "x"},               # BRIDGE_TOKEN absent
    }
    rc = cem.run_check(_MANIFEST, fetch=_fake_fetch(live))
    out = capsys.readouterr().out
    assert rc == 1
    assert "BRIDGE_TOKEN" in out
    assert "svc-b" in out


def test_run_check_passes_when_fleet_is_complete(capsys):
    live = {
        "svc-a": {"DATABASE_URL": "x", "SOME_API_KEY": "x", "SLACK_HOOK": "x",
                  "KNOB_WITH_DEFAULT": "7", "BUILD_STAMP": "ship-x",
                  "BRIDGE_TOKEN": "also-here-fine"},
        "svc-b": {"DATABASE_URL": "x", "BRIDGE_TOKEN": "x"},
    }
    rc = cem.run_check(_MANIFEST, fetch=_fake_fetch(live))
    out = capsys.readouterr().out
    assert rc == 0
    assert "OK" in out


def test_run_check_extras_alone_do_not_fail_but_are_named(capsys):
    live = {
        "svc-a": {"DATABASE_URL": "x", "SOME_API_KEY": "x", "SLACK_HOOK": "x",
                  "PDL_API_KEY": "zombie"},
        "svc-b": {"DATABASE_URL": "x", "BRIDGE_TOKEN": "x"},
    }
    rc = cem.run_check(_MANIFEST, fetch=_fake_fetch(live))
    out = capsys.readouterr().out
    assert rc == 0
    assert "PDL_API_KEY" in out


def test_run_check_never_prints_values(capsys):
    """Secret hygiene: the table names vars, never their values."""
    live = {
        "svc-a": {"DATABASE_URL": "postgres://user:hunter2@host/db",
                  "SOME_API_KEY": "sk-SECRET-VALUE", "SLACK_HOOK": "https://hooks/T000"},
        "svc-b": {"DATABASE_URL": "postgres://user:hunter2@host/db"},
    }
    cem.run_check(_MANIFEST, fetch=_fake_fetch(live))
    out = capsys.readouterr().out
    assert "hunter2" not in out and "sk-SECRET-VALUE" not in out and "hooks/T000" not in out


def test_run_check_fetch_failure_is_fatal_not_vacuous():
    """If railway can't be read for a service, the gate must FAIL, not skip the
    service and pass vacuously (the same philosophy as ship.sh's refusal when
    neither web URL is set)."""
    def fetch(service):
        raise cem.FetchError(f"railway variables failed for {service}")
    rc = cem.run_check(_MANIFEST, fetch=fetch)
    assert rc == 1


# ── the real manifest stays sane ─────────────────────────────────────────────

def test_repo_manifest_parses_and_covers_the_fleet():
    raw = json.loads((_ROOT / "ops" / "env-manifest.json").read_text())
    services, optional = cem.parse_manifest(raw)
    assert set(services) == {"engagement-preview", "discovery-api",
                             "discovery-cron", "linkedin-tofu-cron"}
    for svc, req in services.items():
        assert req, f"{svc} has no required vars — a vacuous gate"
        assert "DATABASE_URL" in req, f"{svc} must require DATABASE_URL"
        assert len(req) == len(set(req)), f"{svc} lists a var twice"
    # The two incident vars that motivated this gate stay pinned.
    assert "REPLYIO_API_KEY" in services["discovery-cron"]
    assert {"N8N_CLAY_DISPATCH_URL", "CLAY_BRIDGE_TOKEN"} <= set(services["linkedin-tofu-cron"])
    assert {"N8N_CLAY_DISPATCH_URL", "CLAY_BRIDGE_TOKEN"} <= set(services["engagement-preview"])
    # Required and optional must not overlap — that would be a contradiction.
    all_req = {v for req in services.values() for v in req}
    assert not (all_req & optional)
