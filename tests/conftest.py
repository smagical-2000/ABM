import pytest


@pytest.fixture(autouse=True)
def _no_real_slack(monkeypatch):
    """Tests must NEVER post to real Slack (2026-07-23: every full-suite run
    leaked a '[ALERT] Notify HELD ... abm_dueco' card to the live ops channel —
    the stress-guard tests inherit .env's webhooks and each fresh tmp repo has
    empty alert-throttle state). Strip every webhook + force the QA tag so even
    a future test that wires its own hook is visibly a drill."""
    for var in ("SLACK_OPS_ALERTS_WEBHOOK", "SLACK_ENGAGEMENT_WEBHOOK",
                "SLACK_AE_WEBHOOK", "SLACK_SDR_WEBHOOK", "SLACK_TOFU_WEBHOOK",
                "SLACK_OUTREACH_WEBHOOK", "SLACK_CHANGELOG_WEBHOOK"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ALERTS_QA_MODE", "1")
