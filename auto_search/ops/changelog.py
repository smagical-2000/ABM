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
    """Pure: a change entry -> a Slack Block Kit payload, written for a
    NON-TECHNICAL reader using the ticket's exact field structure:
    What changed / Why it changed / When it was implemented / Who made the
    change / Status (In progress / Completed / Rolled back). No emoji."""
    headline = _STATUS_HEADLINE.get(entry.status, entry.status)
    header = f"{headline}: {entry.what}"[:150]
    body = [f"*What changed:* {entry.what}"]
    if entry.why:
        body.append(f"*Why it changed:* {entry.why}")
    if entry.summary:
        body.append(f"*In short:* {entry.summary}")
    fields = [
        f"*Status:* {_STATUS_LABEL.get(entry.status, entry.status)}",
        f"*Area:* {_AREA_LABEL.get(entry.area, entry.area)}",
        f"*Who made the change:* {entry.who}",
        f"*When it was implemented:* {_short_date(entry.created_at)}",
    ]
    return {
        "blocks": [
            {"type": "header", "text": {"type": "plain_text", "text": header}},
            {"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(body)}},
            {"type": "section", "fields": [
                {"type": "mrkdwn", "text": f} for f in fields]},
            {"type": "context", "elements": [
                {"type": "mrkdwn",
                 "text": f"Change ref: {entry.change_id} — the started and completed "
                         "messages for one change share this ref."}]},
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
