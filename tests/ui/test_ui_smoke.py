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
from pathlib import Path

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

-- UI Demo Clinic gets a READY warm-intros payload with NO warm paths (all direct),
-- so the drawer's decluttered "no warm contacts" view is exercised + regressed.
UPDATE scored_accounts SET warm_intros =
  '{"state":"ready","source":"apollo","schools_enriched":true,"warm_count":0,
    "founders_used":["Harpaul","Rosie","Geoffrey"],
    "contacts":[
      {"name":"Jane Roe","title":"VP Revenue Cycle","linkedin_url":"https://www.linkedin.com/in/jane-roe","location":"Cincinnati, Ohio","schools":["Xavier University"],"paths":[]},
      {"name":"John Doe","title":"Chief Financial Officer","linkedin_url":"https://www.linkedin.com/in/john-doe","location":"Cincinnati, Ohio","schools":[],"paths":[]}
    ]}'::jsonb
WHERE account_id='ui_demo_social';

-- UI Demo Health System gets a WARM path (warm_count 1) so the Scored board's
-- "N warm" badge + the drawer's warm linkage are exercised + regressed.
UPDATE scored_accounts SET warm_intros =
  '{"state":"ready","source":"apollo","schools_enriched":false,"warm_count":1,
    "founders_used":["Harpaul","Rosie","Geoffrey"],
    "contacts":[{"name":"Warm Exec","title":"VP Revenue Cycle","linkedin_url":"https://www.linkedin.com/in/warm-exec","location":"Dallas, Texas","schools":[],"paths":[{"kind":"shared_employer","founder":"Harpaul","evidence":"Both at Olive — overlapping 2019-2021","strength":80}],"warmth":80}]}'::jsonb
WHERE account_id='ui_demo_job';

-- Discovery: a STACKED company (2 open RCM roles) → exercises the
-- "🔥 N RCM roles open" headline pill on the row + drawer.
INSERT INTO discovery_companies
  (normalized_name, display_name, domain, icp_status, segment, confidence,
   reasoning, hq_state, qualified_at, first_seen_at)
VALUES
  ('uistackhealth','UI Stack Health System','uistack.example','qualified',
   'health_system',0.92,'Stacked revenue-cycle build-out.','TX',
   now()-interval '2 hours', now()-interval '2 hours')
ON CONFLICT (normalized_name) DO UPDATE SET
  icp_status=EXCLUDED.icp_status, qualified_at=EXCLUDED.qualified_at;

INSERT INTO discovery_signals
  (company_id, source, signal_type, source_external_id, summary,
   signal_strength, observed_at, payload)
SELECT dc.id, 'indeed', 'job_posting', s.ext, s.summ, 0.72,
       now()-interval '1 day', s.pl::jsonb
FROM discovery_companies dc,
     (VALUES
       ('uitest-biller','Hiring: Medical Biller',
        '{"role":"Biller","tier":"standard","job_title":"Medical Biller","job_url":"https://example.com/b"}'),
       ('uitest-coder','Hiring: Medical Coder',
        '{"role":"Coder","tier":"standard","job_title":"Medical Coder","job_url":"https://example.com/c"}')
     ) AS s(ext, summ, pl)
WHERE dc.normalized_name='uistackhealth'
ON CONFLICT (source, source_external_id) DO NOTHING;

-- Stacking watch list: a single open standard role → exercises the WatchStrip.
INSERT INTO parked_companies
  (company_key, name, domain, role, roles, postings, state, sample_url,
   sample_title, last_seen_at)
VALUES
  ('uisoloclinic','UI Solo Clinic','uisolo.example','Coder','["Coder"]',1,'OH',
   'https://example.com/solo','Medical Coder', now())
ON CONFLICT (company_key) DO UPDATE SET last_seen_at=EXCLUDED.last_seen_at;
"""


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture(scope="module")
def base_url():
    # Ensure every table exists (incl. parked_companies) before seeding — the
    # local DB may predate newer tables; schema.sql is all CREATE IF NOT EXISTS.
    schema = Path(__file__).resolve().parents[2] / "auto_search" / "db" / "schema.sql"
    subprocess.run(["psql", DB, "-f", str(schema)], capture_output=True, text=True)
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


def test_warm_intros_no_warm_renders_clean(page):
    """When no contact has a warm path, the section drops the warm framing: it's
    titled 'Decision-makers', shows no repeated 'No founder path' line, and the
    footer reads as a plain decision-maker count (not '0 warm of N')."""
    page.click("text=Scored")
    page.wait_for_selector("text=UI Demo Clinic", timeout=10_000)
    page.click("text=UI Demo Clinic")
    page.wait_for_selector("text=Decision-makers", timeout=10_000)   # heading adapts to 0-warm
    assert page.locator("text=Jane Roe").count() > 0                 # the contact still renders
    assert page.locator("text=No founder path").count() == 0         # per-row clutter gone
    assert page.locator("text=0 warm of").count() == 0               # footer reframed
    assert page.get_by_text("no warm paths", exact=False).count() > 0  # but it's mentioned once


def test_scored_board_highlights_warm_accounts(page):
    """An account with warm intro paths gets an 'N warm' badge on its board row;
    a 0-warm account does not."""
    page.click("text=Scored")
    page.wait_for_selector("text=UI Demo Health System", timeout=10_000)
    assert page.get_by_text("1 warm", exact=False).count() > 0       # warm account badged


def test_scored_board_has_find_intros_button(page):
    """The board-level warm-intros backfill: a 'Find intros' action with a
    two-click confirm that previews the count + the green/yellow spend. Must
    render (seeded accounts are high/medium with no intros yet) and not crash."""
    page.click("text=Scored")
    page.wait_for_selector("text=UI Demo Health System", timeout=10_000)
    assert page.locator("text=Find intros").count() > 0          # the batch button
    page.click("text=Find intros")                               # open the confirm
    assert page.locator("text=Find intros for").count() > 0      # count + cost preview
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


def test_stacked_hiring_pill_and_watch_strip(page):
    """Jobs signal-stacking UI: a company with 2 open RCM roles shows the
    '🔥 N RCM roles open' headline, and the parked single-role company surfaces
    in the subtle (expandable) watch strip — all without console errors."""
    page.click("text=Discovery")
    page.wait_for_selector("text=UI Stack Health System", timeout=10_000)
    assert page.locator("text=RCM roles open").count() > 0     # the 🔥 stacked pill
    # The watch strip: parked single-standard-role companies.
    page.wait_for_selector("text=Watching", timeout=10_000)
    page.click("text=Watching")                                # expand the list
    assert page.locator("text=UI Solo Clinic").count() > 0
    real = [e for e in page.console_errors
            if not any(x in e.lower() for x in ("favicon", "tailwind", "cdn", "font"))]
    assert not real, f"console errors: {real[:5]}"


def test_social_listening_panel_opens(page):
    page.click("text=Discovery")
    # Social-listening setup now lives inside the unified "Scan signals" control.
    page.click("text=Scan signals")                                 # open the run popover
    page.click("text=Manage")                                       # → social-listening setup
    page.wait_for_selector("text=Event keywords", timeout=10_000)   # the panel's section
    assert page.locator("text=Monitored accounts").count() > 0       # accounts section header
    assert page.locator("text=Back-fill").count() > 0                # the reframed scan
