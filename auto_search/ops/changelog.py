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
STATUSES = ("initiated", "completed", "rolled_back")
_STATUS_LABEL = {"initiated": "Change initiated",
                 "completed": "Change completed",
                 "rolled_back": "Change rolled back"}
# Change categories from the ticket (kept open — any string is accepted).
AREAS = ("automation_logic", "campaign_rules", "integration_behavior",
         "cadence_scheduling", "scoring", "other")


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
    """Pure: a change entry -> a Slack Block Kit payload. No emoji (matches the
    house style); the status is a plain-text tag so the channel stays scannable."""
    tag = _STATUS_LABEL.get(entry.status, entry.status)
    header = f"[{entry.status.upper()}] {entry.what}"[:150]
    fields = [
        f"*Status:* {tag}",
        f"*Area:* {entry.area}",
        f"*Who:* {entry.who}",
        f"*When:* {_short_date(entry.created_at)}",
    ]
    body = [f"*What:* {entry.what}"]
    if entry.why:
        body.append(f"*Why:* {entry.why}")
    if entry.summary:
        body.append(f"*Summary:* {entry.summary}")
    body.append(f"_id: {entry.change_id}_")
    return {
        "blocks": [
            {"type": "header", "text": {"type": "plain_text", "text": header}},
            {"type": "section", "fields": [
                {"type": "mrkdwn", "text": f} for f in fields]},
            {"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(body)}},
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
