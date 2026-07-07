"""Automation change log — one structured record + Slack post per change.

Every change to the ABM automation (logic, campaign rules, integration
behavior, cadence/scheduling) is logged as a structured entry and pushed to a
shared Slack channel when it's INITIATED and again when it's COMPLETED (or
ROLLED BACK). The team gets one place to see what changed, why, when, and by
whom — so testing has full visibility (MAR2 change-log ticket).

Split like the rest of the Slack code: `build_change_card` is PURE (entry ->
Block Kit payload, fully testable, no I/O); `post_change` does the one bit of
I/O (httpx POST to SLACK_CHANGELOG_WEBHOOK). The persistent list of entries is
owned by the caller (a repo setting) — this module only shapes + posts.
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import UTC, datetime

import httpx
from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)

# The lifecycle a change moves through. `initiated` and `completed` are the two
# the ticket requires a Slack post on; `rolled_back` covers a reverted change.
# Display labels use the ticket's exact wording (non-technical reader first).
STATUSES = ("initiated", "completed", "rolled_back")
_STATUS_LABEL = {"initiated": "In progress",
                 "completed": "Completed",
                 "rolled_back": "Rolled back"}
_STATUS_HEADLINE = {"initiated": "Change started",
                    "completed": "Change completed",
                    "rolled_back": "Change rolled back"}
# Change categories from the ticket (kept open — any string is accepted).
AREAS = ("automation_logic", "campaign_rules", "integration_behavior",
         "cadence_scheduling", "scoring", "other")
_AREA_LABEL = {"automation_logic": "Automation logic",
               "campaign_rules": "Campaign rules",
               "integration_behavior": "Integration behavior",
               "cadence_scheduling": "Cadence or scheduling",
               "scoring": "Scoring",
               "other": "Other"}


class ChangeEntry(BaseModel):
    """One change-log record. `change_id` links an initiated entry to its later
    completed/rolled_back entry so a change reads as one thread."""

    change_id: str = Field(default_factory=lambda: "chg_" + uuid.uuid4().hex[:10])
    what: str                                  # what changed (required)
    why: str = ""                              # why it changed
    area: str = "other"                        # category (see AREAS)
    who: str = "engineering"                   # who made the change
    audience: str = ""                         # who it affects (SDRs / AEs / Marketing…)
    status: str = "initiated"                  # initiated | completed | rolled_back
    summary: str = ""                          # short one-liner for the Slack message
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())

    @field_validator("status")
    @classmethod
    def _known_status(cls, v: str) -> str:
        v = (v or "").strip().lower()
        if v not in STATUSES:
            raise ValueError(f"status must be one of {STATUSES}")
        return v

    @field_validator("what")
    @classmethod
    def _what_required(cls, v: str) -> str:
        if not (v or "").strip():
            raise ValueError("`what` (what changed) is required")
        return v.strip()


def _short_date(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).strftime("%b %d, %H:%M UTC")
    except (ValueError, TypeError):
        return iso or ""


def build_change_card(entry: ChangeEntry) -> dict:
    """Pure: a change entry -> a Slack Block Kit payload in the company's
    Feature-Announcement style — What? / Why? / Who? / When? — written for a
    non-technical reader. "Who?" is the AUDIENCE the change affects (their
    convention), not the author; the author rides in the footer. No emoji."""
    header = entry.what[:150]
    when = {"completed": f"Live now ({_short_date(entry.created_at)})",
            "initiated": f"In progress (started {_short_date(entry.created_at)})",
            "rolled_back": f"Rolled back ({_short_date(entry.created_at)})",
            }.get(entry.status, _short_date(entry.created_at))
    sections = [f"*What?*\n{entry.what}"
                + (f"\n{entry.summary}" if entry.summary else "")]
    if entry.why:
        sections.append(f"*Why?*\n{entry.why}")
    sections.append(f"*Who?*\n{entry.audience or 'GTM team (SDRs, AEs, Marketing)'}")
    sections.append(f"*When?*\n{when}")
    return {
        "blocks": [
            {"type": "header", "text": {"type": "plain_text", "text": header}},
            {"type": "section", "text": {"type": "mrkdwn", "text": "\n\n".join(sections)}},
            {"type": "context", "elements": [
                {"type": "mrkdwn",
                 "text": f"{_AREA_LABEL.get(entry.area, entry.area)} · by {entry.who} · "
                         f"ref {entry.change_id} (started/completed posts for one "
                         "change share this ref)"}]},
        ]
    }


def post_change(entry: ChangeEntry, *, webhook: str | None = None) -> bool:
    """Post the change card to the changelog Slack channel. Returns True on a 2xx.
    No webhook configured -> logs + returns False (never raises), so a missing
    channel never breaks the change itself."""
    hook = webhook or os.getenv("SLACK_CHANGELOG_WEBHOOK")
    if not hook:
        logger.info("changelog: no SLACK_CHANGELOG_WEBHOOK — not posting %s", entry.change_id)
        return False
    try:
        r = httpx.post(hook, json=build_change_card(entry), timeout=15)
        r.raise_for_status()
        return True
    except Exception as e:  # noqa: BLE001 — a Slack hiccup must not fail the change
        logger.warning("changelog post failed for %s: %s", entry.change_id, e)
        return False


# ── Notion mirror ─────────────────────────────────────────────────────
# The same entry, written as a row in the "ABM Automation Change Log" Notion
# database so the team's docs stay in sync automatically. Needs a Notion
# integration token (NOTION_TOKEN) whose integration is shared with the DB, and
# the DB id (NOTION_CHANGELOG_DB_ID). Stable API version so a standard internal
# integration token works.
_NOTION_VERSION = "2022-06-28"
_NOTION_PAGES_URL = "https://api.notion.com/v1/pages"


def _rt(text: str) -> dict:
    return {"rich_text": [{"text": {"content": (text or "")[:1900]}}]}


def notion_properties(entry: ChangeEntry) -> dict:
    """Pure: a change entry -> the Notion page `properties` map, matching the
    change-log database schema (What changed / Why it changed / Status / Area /
    Who made the change / When implemented / Summary / Change ref)."""
    props = {
        "What changed": {"title": [{"text": {"content": entry.what[:1900]}}]},
        "Status": {"select": {"name": _STATUS_LABEL.get(entry.status, entry.status)}},
        "Area": {"select": {"name": _AREA_LABEL.get(entry.area, entry.area)}},
        "Who made the change": _rt(entry.who),
        "Change ref": _rt(entry.change_id),
    }
    if entry.why:
        props["Why it changed"] = _rt(entry.why)
    if entry.summary:
        props["Summary"] = _rt(entry.summary)
    if entry.created_at:
        props["When implemented"] = {"date": {"start": entry.created_at}}
    return props


def post_to_notion(entry: ChangeEntry, *, token: str | None = None,
                   database_id: str | None = None) -> bool:
    """Create a row for this change in the Notion change-log database. Best-effort:
    no token/db configured -> logs + returns False (never raises), so a missing
    Notion setup never breaks the change itself."""
    tok = token or os.getenv("NOTION_TOKEN")
    db = database_id or os.getenv("NOTION_CHANGELOG_DB_ID")
    if not (tok and db):
        logger.info("changelog: NOTION_TOKEN/NOTION_CHANGELOG_DB_ID unset — "
                    "not mirroring %s to Notion", entry.change_id)
        return False
    try:
        r = httpx.post(_NOTION_PAGES_URL, timeout=15,
                       headers={"Authorization": f"Bearer {tok}",
                                "Notion-Version": _NOTION_VERSION,
                                "Content-Type": "application/json"},
                       json={"parent": {"database_id": db},
                             "properties": notion_properties(entry)})
        r.raise_for_status()
        return True
    except Exception as e:  # noqa: BLE001 — a Notion hiccup must not fail the change
        logger.warning("changelog Notion mirror failed for %s: %s", entry.change_id, e)
        return False
