"""Create the ABM LinkedIn campaigns in HeyReach as DRAFTS (never started).

One per vertical with LinkedIn copy in the Notion 'Sunny Email Campaign Info' doc:
connect request note -> (if accepted, +1 day) follow-up message -> end.
Sender-less attempt: no LinkedIn account is connected yet (MAR2-6 pending), so we
try linkedInAccountIds=[] — if the API demands a non-empty sender list, we stop
and report the blocker. Campaigns are DRAFTS either way; nothing can send.
"""
import json
from pathlib import Path

import httpx

_ENV = Path(__file__).resolve().parent.parent / ".env"
key = [line.split("=", 1)[1].strip()
       for line in _ENV.read_text().splitlines()
       if line.startswith("HEYREACH_API_KEY=")][0]
BASE = "https://api.heyreach.io/api/public"
H = {"X-API-KEY": key, "Content-Type": "application/json"}

SCHEDULE = {
    "dailyStartTime": "09:00:00", "dailyEndTime": "17:00:00",
    "timeZoneId": "America/New_York",
    "enabledMonday": True, "enabledTuesday": True, "enabledWednesday": True,
    "enabledThursday": True, "enabledFriday": True,
    "enabledSaturday": False, "enabledSunday": False,
    "startDate": None, "endDate": None,
}

# Notion copy (Sunny Email Campaign Info -> LinkedIn sections), personalization
# via HeyReach placeholders. Connect notes stay under LinkedIn's ~200-char cap.
CAMPAIGNS = [
    {
        "name": "ABM - Health Systems - LinkedIn Connect",
        "note": ("Hi {FIRST_NAME}, I work with health system ops and RCM leaders on "
                 "reducing administrative costs and automating the workflows that are "
                 "quietly draining margins. Would love to connect!"),
        "message": ("Hi {FIRST_NAME} — thanks for connecting.\n\n"
                    "We've been publishing practical content on where health systems are "
                    "finding real margin in 2026 — the operational leaks and automation gaps "
                    "that don't show up cleanly on a budget line.\n\n"
                    "One piece I'd flag for {COMPANY_NAME} specifically: The 7 Silent "
                    "Operational Leaks Draining Health System Margins in 2026.\n\n"
                    "We're also offering a free operational inefficiency report for a "
                    "handful of selected accounts, tailored to your workflows. Happy to put "
                    "one together for {COMPANY_NAME} if it'd be useful."),
    },
    {
        "name": "ABM - Ortho - LinkedIn Connect",
        "note": ("Hi {FIRST_NAME}, I work with orthopedic practices to streamline "
                 "administrative workflows and improve revenue cycle efficiency. "
                 "Would love to connect!"),
        "message": ("Hi {FIRST_NAME} — thanks for connecting.\n\n"
                    "We're working with a small group of orthopedic organizations this month "
                    "to share insights on where AI can meaningfully reduce RCM and "
                    "operational friction. Based on {COMPANY_NAME}'s footprint, we thought "
                    "this might be relevant.\n\n"
                    "Our Head of RCM recorded a short, personalized video for {COMPANY_NAME} "
                    "on how AI is helping teams streamline RCM and day-to-day operations.\n\n"
                    "If it's useful, we're happy to put together a free operational "
                    "inefficiency report tailored to your workflows. No pressure at all — "
                    "just wanted to offer it."),
    },
    {
        "name": "ABM - Behavioral Health - LinkedIn Connect",
        "note": ("Hi {FIRST_NAME}, I help behavioral-health organizations reclaim lost "
                 "revenue and reduce administrative burden. Let's connect!"),
        "message": ("Hi {FIRST_NAME} — thanks for connecting.\n\n"
                    "We work with behavioral health organizations on reclaiming lost revenue "
                    "and reducing the administrative burden on clinical teams — intake, "
                    "eligibility, auths, claims.\n\n"
                    "If it'd be useful for {COMPANY_NAME}, I can share what similar BH "
                    "organizations are automating today, or put together a free operational "
                    "inefficiency report tailored to your workflows."),
    },
]


def sequence(note: str, message: str) -> dict:
    """connect (withdraw 30d) -> if accepted: message after 1 day -> end;
    if never accepted: end (the withdraw handles cleanup)."""
    return {
        "nodeType": "CONNECTION_REQUEST",
        "actionDelay": 0, "actionDelayUnit": "DAY",
        "payload": {
            "messages": [note],
            "fallbackMessage": note.replace("{FIRST_NAME}", "there"),
            "toBeWithdrawnAfterDays": 30,
        },
        "conditionalNode": {
            "nodeType": "MESSAGE",
            "actionDelay": 1, "actionDelayUnit": "DAY",
            "payload": {
                "messages": [message],
                "fallbackMessage": message.replace("{FIRST_NAME}", "there")
                                          .replace("{COMPANY_NAME}", "your organization"),
            },
            # MCP-validated shape: MESSAGE needs BOTH branches — conditional =
            # END (reply-detected exit, 0 delay allowed), unconditional >= 3h.
            "conditionalNode": {"nodeType": "END", "actionDelay": 0,
                                "actionDelayUnit": "HOUR"},
            "unconditionalNode": {"nodeType": "END", "actionDelay": 3,
                                  "actionDelayUnit": "HOUR"},
        },
        "unconditionalNode": {"nodeType": "END", "actionDelay": 1,
                              "actionDelayUnit": "DAY"},
    }


created = []
for c in CAMPAIGNS:
    # One empty USER_LIST per campaign — the bucket our app pushes leads into.
    r = httpx.post(f"{BASE}/list/CreateEmptyList", headers=H,
                   json={"name": c["name"].replace("LinkedIn Connect", "Leads"),
                         "type": "USER_LIST"}, timeout=30)
    print("CreateEmptyList ->", r.status_code, r.text[:120])
    list_id = (r.json() or {}).get("id") if r.status_code == 200 else None

    body = {
        "name": c["name"],
        "linkedInUserListId": list_id,
        "linkedInAccountIds": [],          # no seat connected yet (MAR2-6) — draft only
        "schedule": SCHEDULE,
        "sequence": sequence(c["note"], c["message"]),
    }
    r = httpx.post(f"{BASE}/campaign/Create", headers=H, json=body, timeout=30)
    print("Create", repr(c["name"]), "->", r.status_code)
    if r.status_code != 200:
        print("   ", r.text[:400])
        continue
    out = r.json()
    cid = out.get("id") if isinstance(out, dict) else out
    created.append({"name": c["name"], "campaign_id": cid, "list_id": list_id})
    print("    created:", json.dumps(created[-1]))

print("\nRESULT:", json.dumps(created, indent=1))
