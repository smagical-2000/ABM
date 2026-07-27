"""Read a PUBLIC Airtable shared grid view — generic, source-agnostic.

Airtable renders a shared view client-side: the browser calls
`/v0.3/view/<viewId>/readSharedViewData` carrying a signed `accessPolicy`
minted by the page's own JS. A plain GET (even with the share cookies) 302s to
/login, and the official REST API 403s unless your token was granted the base.
So we do what the WARN connector already did for warntracker.com: drive a
headless browser, intercept that one response, and read the JSON off the wire.

The payload is column-id keyed and select values are choice IDs, so
`decode_shared_view_payload` flattens it into ordinary
`{column name: python value}` dicts — which is a PURE function, so parsing is
unit-tested against a saved payload with no browser involved.

Nothing here knows what the data means; callers map columns to their own
domain. Used by connectors/warntracker.py (WARN notices).
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

_DATA_PATH = "readSharedViewData"
_DEFAULT_TIMEOUT_MS = 60_000
# The grid can be large (WARN: ~79k rows / tens of MB). Refuse anything
# absurd rather than OOM a cron container.
_MAX_PAYLOAD_BYTES = 120_000_000


def _choice_maps(columns: list[dict]) -> dict[str, dict[str, str]]:
    """{column id: {choice id: label}} for every select-ish column."""
    out: dict[str, dict[str, str]] = {}
    for col in columns:
        choices = ((col.get("typeOptions") or {}).get("choices") or {})
        if not choices:
            continue
        out[col["id"]] = {
            cid: (c.get("name") if isinstance(c, dict) else str(c))
            for cid, c in choices.items()
        }
    return out


def _resolve(value: Any, choices: dict[str, str] | None) -> Any:
    """Map select choice IDs to their labels; pass everything else through."""
    if not choices:
        return value
    if isinstance(value, str):
        return choices.get(value, value)
    if isinstance(value, list):
        return [choices.get(v, v) if isinstance(v, str) else v for v in value]
    return value


def decode_shared_view_payload(payload: str | dict) -> list[dict[str, Any]]:
    """Flatten a readSharedViewData payload into column-NAME keyed rows. PURE.

    Unknown/renamed columns are passed through under whatever name the view
    reports, so a publisher renaming a column degrades to a missing key in the
    caller's mapping rather than an exception here.
    """
    doc = json.loads(payload) if isinstance(payload, str) else payload
    data = doc.get("data", doc)
    table = data.get("table") or {}
    columns = table.get("columns") or []
    if not columns:
        raise ValueError("shared view payload carries no columns")
    names = {c["id"]: c.get("name") or c["id"] for c in columns}
    choices = _choice_maps(columns)
    rows: list[dict[str, Any]] = []
    for row in table.get("rows") or []:
        cells = row.get("cellValuesByColumnId") or {}
        flat = {names[cid]: _resolve(v, choices.get(cid))
                for cid, v in cells.items() if cid in names}
        if flat:
            flat["_airtable_row_id"] = row.get("id")
            rows.append(flat)
    return rows


async def fetch_shared_view_rows(share_url: str, *,
                                 timeout_ms: int = _DEFAULT_TIMEOUT_MS
                                 ) -> list[dict[str, Any]]:
    """Open a public Airtable share and return its rows. Raises on failure.

    Raising (rather than returning []) is deliberate: an empty list from a
    broken fetch is indistinguishable from a genuinely empty table, and this
    codebase has been bitten repeatedly by upstream failures that reported
    success (the Apify quota 403s, the frozen WARN feed).
    """
    from playwright.async_api import async_playwright

    captured: dict[str, str] = {}

    logger.info("airtable share: launching headless browser for %s", share_url)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await (await browser.new_context()).new_page()

        async def _capture(route):
            # route.fetch() replays the request and hands us the real response
            # body; page.on("response") loses it (the CDP inspector cache is
            # evicted before a large body can be read).
            try:
                resp = await route.fetch()
                # .text() (not .body()) — Airtable serves this br/gzip-encoded,
                # and the raw bytes are not JSON. Check the URL BEFORE reading
                # so we never try to decode an unrelated binary response.
                if resp.status == 200 and _DATA_PATH in route.request.url:
                    text = await resp.text()
                    if len(text) > _MAX_PAYLOAD_BYTES:
                        raise ValueError(f"payload too large: {len(text)} chars")
                    captured["body"] = text
                await route.fulfill(response=resp)
            except Exception as err:  # noqa: BLE001 — never wedge the page
                logger.debug("airtable share: route replay failed: %s", err)
                try:
                    await route.continue_()
                except Exception:  # noqa: BLE001 — already handled/closed
                    pass

        await page.route(f"**/*{_DATA_PATH}*", _capture)
        try:
            # NOT networkidle: Airtable holds sockets open, so it never fires.
            await page.goto(share_url, wait_until="domcontentloaded",
                            timeout=timeout_ms)
            # Poll for the intercepted body rather than for a rendered DOM: on
            # a large grid the renderer can die (or the page navigate) AFTER
            # the data response has already been captured, and that capture is
            # all we need. Any page-side error here is only fatal if we ended
            # up with nothing, which the check below reports precisely.
            deadline = time.monotonic() + timeout_ms / 1000
            while "body" not in captured and time.monotonic() < deadline:
                try:
                    await page.wait_for_timeout(500)
                except Exception:  # noqa: BLE001 — page/browser gone
                    break
        except Exception as err:  # noqa: BLE001
            if "body" not in captured:
                raise
            logger.debug("airtable share: page error after capture: %s", err)
        finally:
            await browser.close()

    if "body" not in captured:
        raise RuntimeError(
            f"airtable share: no {_DATA_PATH} response captured for {share_url} "
            "— the share may have been revoked, made private, or Airtable "
            "changed its data endpoint")
    rows = decode_shared_view_payload(captured["body"])
    logger.info("airtable share: decoded %d rows", len(rows))
    return rows
