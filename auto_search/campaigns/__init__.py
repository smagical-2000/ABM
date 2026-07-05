"""Campaign automation (Phase 3) — the WRITE side of the engagement loop.

Phase 1 scores accounts, Phase 2 captures their engagement heat; this module
closes the loop by enrolling the right accounts' contacts into the right
Reply.io email sequence. The app is the orchestration brain only: contacts and
all sending (drip, throttle, mailbox rotation) stay in Reply.io.

    catalog.py — pure ICP -> sequence-key mapping (which sequence an account gets)
    enroll.py  — pure eligibility + contact planning (who qualifies, who gets sent)
    runner.py  — the one place I/O happens (Reply.io write + ledger + Slack)

Spec: docs/CAMPAIGN_AUTOMATION_ARCHITECTURE.md. Ledger: db/campaign_repository.py.
"""
