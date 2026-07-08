"""Capture live platform screenshots for documentation. Drives a headless browser
against the deployed app using basic-auth creds from the env (run via
`railway run --service engagement-preview -- python3 scripts/docshots.py <section>`
so the creds are injected, never printed). Read-only — only navigates + screenshots.
"""
import os
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

URL = os.environ.get("DOCS_URL", "https://engagement-preview-production.up.railway.app/")
USER = os.environ.get("BASIC_AUTH_USER")
PW = os.environ.get("BASIC_AUTH_PASS")
OUT = Path("/Users/sunnydsouza/projects/abm-scorer/docs/images")
SECTION = sys.argv[1] if len(sys.argv) > 1 else "discovery"


def shot(pg, name):
    OUT.mkdir(parents=True, exist_ok=True)
    pg.screenshot(path=str(OUT / f"{name}.png"))
    print("saved", name)


def safe(label, fn):
    try:
        fn()
        print("ok:", label)
    except Exception as e:  # noqa: BLE001 — one bad selector mustn't kill the run
        print(f"SKIP {label}: {type(e).__name__} {str(e)[:120]}")


def click_first_company(pg):
    """Open a company drawer by clicking the first company row name."""
    for _nm in pg.locator("h3, [role='button']").all():
        pass
    # company names render as clickable bold text in each row; click the first that
    # sits above a 'Score' button row. Fall back to any visible org-looking heading.
    pg.locator("text=/^[A-Z].{3,40}$/").first.click(timeout=8000)


def discovery(pg):
    pg.goto(URL, wait_until="networkidle", timeout=60000)
    pg.wait_for_timeout(3000)
    shot(pg, "disc_01_panel")

    # company drawer FIRST (clean page, no popover to intercept the click): click the
    # first company-name heading by its on-screen box -> the row's onClick opens it.
    # Then capture "View evidence" from inside the drawer.
    def _drawer():
        h3 = pg.locator("h3")
        for i in range(min(h3.count(), 12)):
            box = h3.nth(i).bounding_box()
            if box and box["y"] > 300:                # below the stat cards = a company row
                print(f"DIAG drawer click h3[{i}] @y={int(box['y'])}")
                pg.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
                pg.wait_for_timeout(2000)
                shot(pg, "disc_04_company_drawer")
                try:
                    pg.click("text=View evidence", timeout=4000)
                    pg.wait_for_timeout(1500)
                    shot(pg, "disc_05_view_evidence")
                except Exception:  # noqa: BLE001
                    print("SKIP view evidence (button not found)")
                pg.keyboard.press("Escape")
                pg.wait_for_timeout(600)
                return
        raise RuntimeError("no company heading found")
    safe("company drawer + evidence", _drawer)

    def _needs():
        pg.get_by_text("Needs review", exact=False).first.click(timeout=8000)
        pg.wait_for_timeout(1500)
        shot(pg, "disc_03_needs_review")
        pg.get_by_text("Qualified", exact=False).first.click(timeout=5000)
    safe("needs review tab", _needs)

    safe("intent popover", lambda: (pg.click("text=How intent is scored", timeout=8000),
                                    pg.wait_for_timeout(1200), shot(pg, "disc_02_intent_scored"),
                                    pg.keyboard.press("Escape"), pg.wait_for_timeout(400)))


def nr(pg):
    pg.goto(URL, wait_until="networkidle", timeout=60000)
    pg.wait_for_timeout(2500)
    btns = pg.get_by_role("button")
    for i in range(btns.count()):
        if "Needs review" in (btns.nth(i).inner_text() or ""):
            btns.nth(i).click()
            pg.wait_for_timeout(1800)
            shot(pg, "disc_03_needs_review")
            print("ok needs-review")
            return
    print("SKIP: needs-review button not found")


def scored(pg):
    pg.goto(URL, wait_until="networkidle", timeout=60000)
    pg.wait_for_timeout(2000)
    pg.get_by_text("Scored", exact=False).first.click(timeout=8000)
    pg.wait_for_timeout(2800)
    shot(pg, "scored_01_board")

    # open the first scored account -> the deep-research drawer, then scroll it to
    # capture the dossier and warm-intros sections lower down.
    def _drawer():
        h3 = pg.locator("h3")
        for i in range(min(h3.count(), 14)):
            box = h3.nth(i).bounding_box()
            if box and box["y"] > 300:
                print(f"DIAG scored drawer click h3[{i}] @y={int(box['y'])}")
                pg.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
                pg.wait_for_timeout(2200)
                shot(pg, "scored_02_drawer_top")
                pg.mouse.move(1150, 450)
                for _n, nm in ((1, "scored_03_drawer_mid"), (2, "scored_04_drawer_bottom")):
                    pg.mouse.wheel(0, 900)
                    pg.wait_for_timeout(1200)
                    shot(pg, nm)
                return
        raise RuntimeError("no scored account heading found")
    safe("scored drawer", _drawer)


def landing(pg):
    pg.goto(URL, wait_until="networkidle", timeout=60000)
    pg.wait_for_timeout(2000)
    pg.get_by_text("Scored", exact=False).first.click(timeout=8000)
    pg.wait_for_timeout(2800)
    h3 = pg.locator("h3")
    for i in range(min(h3.count(), 14)):
        box = h3.nth(i).bounding_box()
        if box and box["y"] > 300:
            pg.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
            pg.wait_for_timeout(2200)
            try:
                pg.click("text=Open Landing Page", timeout=6000)
                pg.wait_for_timeout(2500)
                shot(pg, "scored_05_landing_page")
                print("ok landing page")
            except Exception as e:  # noqa: BLE001
                print("SKIP landing:", str(e)[:100])
            return
    print("SKIP: no scored account heading")


def news(pg):
    pg.goto(URL, wait_until="networkidle", timeout=60000)
    pg.wait_for_timeout(2000)
    pg.get_by_text("News", exact=True).first.click(timeout=8000)
    pg.wait_for_timeout(3000)
    shot(pg, "news_01_feed")
    # filter to a single topic chip to show the topic filtering
    try:
        pg.get_by_role("button", name="Prior Auth").first.click(timeout=5000)
        pg.wait_for_timeout(2000)
        shot(pg, "news_02_topic_prior_auth")
        print("ok news topic")
    except Exception as e:  # noqa: BLE001
        print("SKIP news topic:", str(e)[:100])


def watch(pg):
    pg.goto(URL, wait_until="networkidle", timeout=60000)
    pg.wait_for_timeout(2000)
    pg.get_by_text("Watch list", exact=True).first.click(timeout=8000)
    pg.wait_for_timeout(3000)
    shot(pg, "watch_01_top")
    # scroll to capture the parked section lower down
    pg.mouse.move(700, 450)
    pg.mouse.wheel(0, 950)
    pg.wait_for_timeout(1200)
    shot(pg, "watch_02_parked")
    print("ok watch")


def _click_first_row(pg, ymin=320):
    """Click the left side (account name) of the first clickable heat-board row."""
    box = pg.evaluate(
        """(ymin)=>{
            for (const el of document.querySelectorAll('div')){
                const cs=getComputedStyle(el);
                if(cs.cursor!=='pointer') continue;
                const r=el.getBoundingClientRect();
                if(r.width>520 && r.top>ymin && r.height>40 && r.height<96)
                    return {x:Math.round(r.x+40), y:Math.round(r.top+r.height/2)};
            }
            return null;
        }""", ymin)
    if box:
        pg.mouse.click(box["x"], box["y"])
        return True
    return False


def engagement(pg):
    pg.goto(URL, wait_until="networkidle", timeout=60000)
    pg.wait_for_timeout(2500)
    pg.get_by_text("Engagement", exact=True).first.click(timeout=8000)
    pg.wait_for_timeout(4000)
    shot(pg, "eng_01_activity")

    def _accounts_tab():
        btns = pg.get_by_role("button")
        for i in range(btns.count()):
            if (btns.nth(i).inner_text() or "").strip().startswith("Accounts"):
                btns.nth(i).click()
                pg.wait_for_timeout(2200)
                shot(pg, "eng_02_accounts")
                return
        raise RuntimeError("accounts tab button not found")
    safe("accounts tab", _accounts_tab)

    def _drawer():
        if not _click_first_row(pg):
            raise RuntimeError("no clickable account row")
        pg.wait_for_timeout(2500)
        shot(pg, "eng_03_drawer_top")
        # scroll the drawer body to reveal breakdown + timeline
        pg.mouse.move(1150, 450)
        pg.mouse.wheel(0, 700)
        pg.wait_for_timeout(1200)
        shot(pg, "eng_04_drawer_timeline")
    safe("engagement drawer", _drawer)

    # Activate modal (Slack preview) — screenshot only, never click "Post to Slack".
    def _modal():
        pg.get_by_role("button", name="Activate to SDR").first.click(timeout=5000)
        pg.wait_for_timeout(2500)
        shot(pg, "eng_05_activate_modal")
    safe("activate modal", _modal)


def probe(pg):
    """Diagnostic: dump the markup of the first company row so we can target the
    click that opens the detail panel."""
    pg.goto(URL, wait_until="networkidle", timeout=60000)
    pg.wait_for_timeout(3000)
    targets = pg.evaluate(
        """() => {
            const out = [];
            for (const el of document.querySelectorAll('*')) {
                const cs = getComputedStyle(el);
                if (cs.cursor !== 'pointer') continue;
                const r = el.getBoundingClientRect();
                const t = (el.textContent || '').trim().replace(/\\s+/g, ' ').slice(0, 45);
                if (t && r.width > 120 && r.top > 230 && r.top < 520)
                    out.push({t, x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2),
                              w: Math.round(r.width), tag: el.tagName});
            }
            return out.slice(0, 16);
        }"""
    )
    for t in targets:
        print(t)


def main():
    if not (USER and PW):
        print("ERROR: BASIC_AUTH_USER/PASS not in env (run via `railway run`)")
        return 1
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(http_credentials={"username": USER, "password": PW},
                                  viewport={"width": 1440, "height": 900}, device_scale_factor=2)
        pg = ctx.new_page()
        {"discovery": discovery, "scored": scored, "probe": probe, "nr": nr, "landing": landing, "news": news, "watch": watch, "engagement": engagement}.get(SECTION, discovery)(pg)
        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
