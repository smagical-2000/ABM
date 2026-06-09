"""Browser smoke + interaction tests for the Discovery/Scoring UI (Playwright).

Why this exists: the app compiles JSX in-browser (Babel), so a syntax or wiring
bug white-screens the page and never shows in pytest. These drive a real headless
Chromium against a locally-run server (seeded local Postgres, auth disabled) and
assert the page renders, has no console errors, and the key flows work — the
discovery signal filter, the scored "Why discovered" panel, Monitored Accounts.

Run explicitly (needs local Postgres + the playwright chromium build):
    python3 -m pytest tests/ui/test_ui_smoke.py -v

Skipped automatically if Playwright or its browser isn't installed, so it never
breaks the normal unit run.
"""

from __future__ import annotations

import os
import socket
import subprocess
import time
import urllib.request

import pytest

pytest.importorskip("playwright")
from playwright.sync_api import sync_playwright  # noqa: E402

DB = "postgresql://localhost/abm_discovery"

# Two scored accounts that exercise the "Why discovered" panel: a job-posting
# lead (Baptist-style) and a social-engagement lead — both with a proof URL.
_SEED_SQL = """
INSERT INTO scored_accounts
  (account_id, source, name, segment, framework, domain, state, max_total, total,
   tier_band, tier_label, dimensions, recommendation, model, cost_usd,
   discovery_signals, scored_at, created_at, updated_at)
VALUES
 ('ui_demo_job','discovery','UI Demo Health System','health_system','health_system',
  'uidemo.example','scored',27,23,'high','Tier 1',
  '[{"key":"npr","label":"Net Patient Revenue","score":10,"max":10,"summary":"$1.5B NPR."}]'::jsonb,
  'Strong Tier 1 fit.','claude-sonnet-4-5',0.09,
  '[{"signal_type":"job_posting","summary":"Hiring: Senior Revenue Cycle Supervisor","url":"https://example.com/job/123"}]'::jsonb,
  now()-interval '3 hours','now','now'),
 ('ui_demo_social','discovery','UI Demo Clinic','specialty','specialty',
  'uidemoclinic.example','scored',30,24,'high','High Fit',
  '[{"key":"intent","label":"Business Intent","score":9,"max":10,"summary":"Engaged."}]'::jsonb,
  'Prioritize.','claude-sonnet-4-5',0.09,
  '[{"signal_type":"social_engagement","summary":"Dr. Jane Doe (VP Revenue Cycle) engaged with a Magical post","url":"https://www.linkedin.com/feed/x"}]'::jsonb,
  now()-interval '1 hours','now','now'),
 -- Bogus framework key (config skew): opening this MUST NOT white-screen the app.
 ('ui_demo_badfw','discovery','UI Demo Legacy Co','health_system','legacy_unknown_v9',
  'uidemolegacy.example','scored',27,20,'medium','Tier 2',
  '[{"key":"npr","label":"Net Patient Revenue","score":7,"max":10,"summary":"NPR."}]'::jsonb,
  'Review.','claude-sonnet-4-5',0.09,
  '[{"signal_type":"leadership_change","summary":"New CFO appointed"}]'::jsonb,
  now()-interval '2 hours','now','now')
ON CONFLICT (account_id) DO UPDATE SET
  discovery_signals=EXCLUDED.discovery_signals, scored_at=EXCLUDED.scored_at,
  total=EXCLUDED.total, tier_band=EXCLUDED.tier_band, dimensions=EXCLUDED.dimensions;
"""


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture(scope="module")
def base_url():
    # Seed the local DB (best-effort; skip the whole module if no local Postgres).
    seed = subprocess.run(["psql", DB, "-c", _SEED_SQL], capture_output=True, text=True)
    if seed.returncode != 0:
        pytest.skip(f"no local Postgres to seed: {seed.stderr[:120]}")

    port = _free_port()
    env = {**os.environ, "DATABASE_URL": DB}
    env.pop("BASIC_AUTH_USER", None)
    env.pop("BASIC_AUTH_PASS", None)
    proc = subprocess.Popen(
        ["python3", "-m", "uvicorn", "auto_search.api.app:app",
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        env=env)
    url = f"http://127.0.0.1:{port}"
    try:
        for _ in range(40):
            try:
                if urllib.request.urlopen(f"{url}/api/health", timeout=1).status == 200:
                    break
            except Exception:  # noqa: BLE001
                time.sleep(0.5)
        else:
            pytest.fail("server did not start")
        yield url
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


@pytest.fixture(scope="module")
def browser():
    try:
        pw = sync_playwright().start()
        b = pw.chromium.launch(headless=True)
    except Exception as e:  # noqa: BLE001 — browser not installed
        pytest.skip(f"playwright chromium unavailable: {e}")
    yield b
    b.close()
    pw.stop()


@pytest.fixture
def page(browser, base_url):
    """A FRESH page per test (no state bleed between interactions)."""
    errors: list[str] = []
    pg = browser.new_page()
    pg.set_default_timeout(15_000)
    pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(base_url, wait_until="networkidle")
    pg.wait_for_selector("text=Scored", timeout=20_000)  # app mounted
    pg.console_errors = errors  # type: ignore[attr-defined]
    yield pg
    pg.close()


def test_app_renders_without_console_errors(page):
    assert page.locator("text=Magical").count() > 0
    # Ignore benign CDN/font noise; fail on real JS/React errors.
    real = [e for e in page.console_errors
            if not any(x in e.lower() for x in ("favicon", "tailwind", "cdn", "font"))]
    assert not real, f"console errors: {real[:5]}"


def test_discovery_signal_filter_has_social_types(page):
    page.click("text=Discovery")
    options = page.locator("select").nth(1).locator("option").all_inner_texts()
    assert "Engaged" in options, options
    assert "Event" in options, options


def test_scored_drawer_shows_why_discovered_with_proof(page):
    page.click("text=Scored")
    page.wait_for_selector("text=UI Demo Health System", timeout=10_000)
    page.click("text=UI Demo Health System")
    page.wait_for_selector("text=Why discovered", timeout=10_000)
    assert page.locator("text=Senior Revenue Cycle Supervisor").count() > 0
    assert page.locator("text=Hiring").count() > 0          # the signal chip
    assert page.locator("a:has-text('proof')").count() > 0  # the evidence link


def test_unknown_framework_drawer_does_not_white_screen(page):
    """Regression: a scored account whose framework the UI config doesn't have
    (version skew) must still open its drawer, not crash the whole app."""
    page.click("text=Scored")
    page.wait_for_selector("text=UI Demo Legacy Co", timeout=10_000)
    page.click("text=UI Demo Legacy Co")
    page.wait_for_selector("text=Re-score", timeout=10_000)   # drawer opened
    assert page.locator("text=Magical").count() > 0           # app still mounted
    assert page.locator("text=Why discovered").count() > 0    # signal still shown


def test_social_listening_panel_opens(page):
    page.click("text=Discovery")
    page.click("text=Social listening")
    page.wait_for_selector("text=Event keywords", timeout=10_000)   # the panel's new section
    assert page.locator("text=Monitored accounts").count() > 0       # accounts section header
    assert page.locator("text=Back-fill").count() > 0                # the reframed scan
