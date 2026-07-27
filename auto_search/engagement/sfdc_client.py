"""Salesforce client — read feeds + one deliberate write.

Pulls CRM engagement (booked meetings + open/won opportunities) for the engagement
phase. Every read is a SOQL SELECT. The ONE write is `create_lead` — used only by the
LinkedIn TOFU ad-engagement flow to create a Lead (replicating the old Zapier step);
it is never called by any sync. `lead_exists` backs the dedup check before that write.

Auth: OAuth 2.0 **Client Credentials Flow** — no user password. The External Client
App "ABM Engagement Sync" runs as a fixed integration user (ops@); we exchange
SFDC_CLIENT_ID + SFDC_CLIENT_SECRET for a short-lived access token, then query the
REST API. Creds come from .env via os.getenv (never logged).

Sync (httpx.Client) — the API calls this from a threadpool handler, like the podcast
sync. Pages /query via `nextRecordsUrl` until done, yielding raw records.
"""

from __future__ import annotations

import logging
import os
import re
import time
from collections.abc import Iterator

import httpx

logger = logging.getLogger(__name__)

_API_VERSION = "v60.0"
_PAGE_CAP = 500          # hard stop so a bad nextRecordsUrl can't loop forever
_MAX_RETRIES = 2         # per page, on 429/5xx — plus at most ONE 401 re-auth
_BACKOFF_CAP_SECONDS = 30.0
# Floor for the high-intent lead pull — leads older than this are stale; we only
# track the 2026-onward cohort (per the user). Open-ended (since -> now).
SINCE_DEFAULT = "2026-01-01"
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_CTRL_RE = re.compile(r"[\x00-\x1f\x7f]")


def soql_quote(value) -> str:
    """Escape a value for use INSIDE a SOQL string literal ('...').

    The ONE place SOQL escaping is defined. NEVER interpolate a dynamic value
    into a query without it — `lead_exists` takes its email straight from
    LinkedIn lead-gen form payloads (external, attacker-supplied) and runs it
    against the production org. SOQL has no stacked statements, so the blast
    radius of a missed escape is a broken/over-broad query rather than a write,
    but the escape being bespoke and inline is how the next WHERE clause ends up
    with none at all.

    Backslash FIRST, then the single quote — the other order leaves the
    backslash we just introduced unescaped and re-opens the literal. Control
    characters (newline, NUL) are dropped: never legitimate inside a literal.
    """
    if value is None:
        return ""
    s = _CTRL_RE.sub("", str(value))
    return s.replace("\\", "\\\\").replace("'", "\\'")


class SalesforceClient:
    """Thin sync, read-only client over the Salesforce REST/SOQL API.

    Pass `http` (an httpx.Client) to inject a transport in tests; otherwise a client
    is opened per call. Credentials default to the SFDC_* env vars.
    """

    def __init__(self, *, client_id: str | None = None, client_secret: str | None = None,
                 login_url: str | None = None, http: httpx.Client | None = None,
                 api_version: str = _API_VERSION, timeout: float = 60.0) -> None:
        self._cid = client_id or os.getenv("SFDC_CLIENT_ID")
        self._secret = client_secret or os.getenv("SFDC_CLIENT_SECRET")
        self._login = (login_url or os.getenv("SFDC_LOGIN_URL") or "").rstrip("/")
        if not (self._cid and self._secret and self._login):
            raise RuntimeError(
                "SFDC_CLIENT_ID / SFDC_CLIENT_SECRET / SFDC_LOGIN_URL not set in .env")
        self._http = http
        self._api = api_version
        self._timeout = timeout
        self._instance: str | None = None
        self._token: str | None = None

    # ── auth ────────────────────────────────────────────────────────────

    def _authenticate(self) -> None:
        """Exchange client id/secret for an access token (client-credentials grant).
        Caches the token + instance_url on the client for the run."""
        resp = self._send("POST", f"{self._login}/services/oauth2/token", data={
            "grant_type": "client_credentials",
            "client_id": self._cid, "client_secret": self._secret,
        })
        resp.raise_for_status()
        tok = resp.json()
        self._token = tok["access_token"]
        self._instance = tok["instance_url"].rstrip("/")

    def _ensure_auth(self) -> None:
        if not self._token:
            self._authenticate()

    # ── read feeds ──────────────────────────────────────────────────────

    def query(self, soql: str) -> Iterator[dict]:
        """Yield every record for a SOQL SELECT, following nextRecordsUrl paging.
        READ ONLY — callers must pass a SELECT.

        Any dynamic value in `soql` MUST be wrapped in `soql_quote()` (or, for
        dates, validated against _DATE_RE). Raw f-string interpolation of
        external data is banned."""
        self._ensure_auth()
        path = f"/services/data/{self._api}/query"
        params: dict | None = {"q": soql}
        for _ in range(_PAGE_CAP):
            resp = self._get_with_retry(f"{self._instance}{path}", params=params)
            resp.raise_for_status()
            data = resp.json()
            yield from data.get("records", [])
            nxt = data.get("nextRecordsUrl")
            if data.get("done", True) or not nxt:
                return
            path, params = nxt, None       # nextRecordsUrl is a full path w/ the cursor
        logger.warning("salesforce query hit the %d-page cap — results truncated", _PAGE_CAP)

    # High-intent inbound leads, per the org's "High Intent Leads" reports: the
    # definition is LeadSource ∈ this set (contact/sales forms + BOFU sources).
    HIGH_INTENT_SOURCES = (
        "Sales Contact Form", "S+G Contact Form", "BOFU",
        "Intercom", "Galyna Brief", "LGF Linkedin", "LGF FB",
    )

    def iter_high_intent_leads(self, *, since: str = SINCE_DEFAULT) -> Iterator[dict]:
        """High-intent inbound leads created on/after `since` (YYYY-MM-DD), with the
        fields we cross + score on. Mirrors the org's High Intent Leads report filter
        (LeadSource in the high-intent set)."""
        if not _DATE_RE.match(since):       # interpolated into SOQL — keep it a bare date
            raise ValueError(f"since must be YYYY-MM-DD, got {since!r}")
        sources = ", ".join(f"'{soql_quote(s)}'" for s in self.HIGH_INTENT_SOURCES)
        yield from self.query(
            "SELECT Id, FirstName, LastName, Company, Email, BN_Email_Domain__c, "
            "Website, Title, LeadSource, Status, Rating, MQL__c, Seats_Requested__c, "
            "In_Healthcare__c, Primary_Purpose__c, Employee_Range__c, IsConverted, "
            "CreatedDate FROM Lead "
            # COMPOUND BOFU (2026-07-20): marketing writes campaign-prefixed
            # sources like 'CS Headspace | BOFU' — Griffen's High-Intent
            # dashboard counts them, so the exact IN-list silently dropped every
            # compound-BOFU inbound (Trevor/FCS, Wendy/Optum). The LIKE arm
            # keeps us aligned with the dashboard definition.
            f"WHERE (LeadSource IN ({sources}) OR LeadSource LIKE '%| BOFU') "
            f"AND CreatedDate >= {since}T00:00:00Z "
            "ORDER BY CreatedDate DESC")

    def iter_tradeshow_leads(self, *, since: str = SINCE_DEFAULT) -> Iterator[dict]:
        """Tradeshow leads that booked a meeting — LeadSource='Trade Show' AND
        Status='Qualified' (the org's "Tradeshow tracking" Qualified stage), created
        on/after `since`. Tradeshow__c carries the show name for display."""
        if not _DATE_RE.match(since):
            raise ValueError(f"since must be YYYY-MM-DD, got {since!r}")
        yield from self.query(
            "SELECT Id, FirstName, LastName, Company, Email, BN_Email_Domain__c, "
            "Website, Title, LeadSource, Status, Tradeshow__c, CreatedDate FROM Lead "
            "WHERE LeadSource = 'Trade Show' AND Status = 'Qualified' "
            f"AND CreatedDate >= {since}T00:00:00Z ORDER BY CreatedDate DESC")

    def iter_low_intent_leads(self, *, since: str = SINCE_DEFAULT) -> Iterator[dict]:
        """TOFU low-intent leads — gated content/guide downloads, tagged by the org as
        LeadSource '… | TOFU' (e.g. '6 UM Trends 2026 | TOFU'). Created on/after
        `since`. Lower buying intent than the contact-form high-intent leads."""
        if not _DATE_RE.match(since):
            raise ValueError(f"since must be YYYY-MM-DD, got {since!r}")
        yield from self.query(
            "SELECT Id, FirstName, LastName, Company, Email, BN_Email_Domain__c, "
            "Website, Title, LeadSource, Status, CreatedDate FROM Lead "
            # 'TOFU Engagement Campaign' (2026-07-20): the Airtable automation's
            # label for OUR OWN LinkedIn-capture echoes pushed into SFDC. Pulled
            # here so a capture the runner missed still scores; the sync then
            # suppresses true echoes via sfdc.filter_tofu_echoes so nobody we
            # already scored at capture time counts twice.
            "WHERE (LeadSource LIKE '%| TOFU' "
            "OR LeadSource = 'TOFU Engagement Campaign') "
            f"AND CreatedDate >= {since}T00:00:00Z ORDER BY CreatedDate DESC")

    # iter_sales_accepted_opportunities was removed in the 2026-06 review — SAO is
    # retired (replaced by meeting_booked / iter_meetings below; see DEPRECATED_KINDS).

    def iter_meetings(self, *, days: int = 180) -> Iterator[dict]:
        """Booked meetings (Event Type='Meeting') created in the last `days`, with
        the related Account's name + website for crossing."""
        yield from self.query(
            "SELECT Id, Subject, Type, AccountId, Account.Name, Account.Website, "
            "WhoId, Who.Name, Who.Type, StartDateTime, ActivityDateTime, CreatedDate "
            "FROM Event "
            f"WHERE Type = 'Meeting' AND CreatedDate = LAST_N_DAYS:{int(days)} "
            "ORDER BY CreatedDate DESC")

    def iter_opportunities(self) -> Iterator[dict]:
        """Active deals — open (not closed) or won — with the related Account's name
        + website for crossing. (Closed-lost is excluded: not an engagement signal.)"""
        yield from self.query(
            "SELECT Id, Name, StageName, IsClosed, IsWon, Amount, AccountId, "
            "Account.Name, Account.Website, CreatedDate, CloseDate "
            "FROM Opportunity WHERE IsClosed = false OR IsWon = true "
            "ORDER BY CreatedDate DESC")

    # ── write (LinkedIn TOFU flow only) ─────────────────────────────────

    def lead_exists(self, email: str) -> bool:
        """True if a Lead with this email already exists — the dedup gate before
        create_lead, so the hourly run never double-creates the same person."""
        e = (email or "").strip()
        if not e:
            return False
        return next(self.query(
            f"SELECT Id FROM Lead WHERE Email = '{soql_quote(e)}' LIMIT 1"),
            None) is not None

    def create_lead(self, fields: dict, *, assignment_rules: bool = True) -> dict:
        """Create a Salesforce Lead. WRITE. Returns the API result {id, success, errors}.

        `assignment_rules=True` sends the `Sforce-Auto-Assign` header — the Zapier
        "Use Assignment Rules: true" toggle. The caller builds `fields` via
        linkedin_ads.build_lead_payload (LastName + Company required)."""
        self._ensure_auth()
        url = f"{self._instance}/services/data/{self._api}/sobjects/Lead"
        headers = {"Authorization": f"Bearer {self._token}",
                   "Content-Type": "application/json"}
        if assignment_rules:
            headers["Sforce-Auto-Assign"] = "true"
        resp = self._send("POST", url, json=fields, headers=headers)
        resp.raise_for_status()
        return resp.json()

    # ── transport ───────────────────────────────────────────────────────

    def _get_with_retry(self, url: str, *, params: dict | None) -> httpx.Response:
        """One READ request with a bounded retry. GET-class only — writes
        (create_lead) are never retried, since a replayed POST duplicates a Lead.

        Unlike the Reply.io / HeyReach / Airtable clients, this one used to
        raise_for_status() on the first non-2xx with zero retries, and the
        client-credentials token cached in _authenticate was never refreshed.
        So a 2-minute instance maintenance 503, or a token expiring partway
        through the daily pull (high-intent + tradeshow + TOFU + meetings),
        failed the whole SFDC leg: BOFU heat lands a day late and the daily-cron
        FAILED alert pages for a transient.

        429/5xx back off (honouring Retry-After) up to _MAX_RETRIES; a 401
        clears the cached token and re-authenticates ONCE, then replays the
        page. Both budgets are bounded, so this always terminates.
        """
        attempt, reauthed = 0, False
        while True:
            resp = self._send("GET", url, params=params,
                              headers={"Authorization": f"Bearer {self._token}"})
            if resp.status_code == 401 and not reauthed:
                logger.info("salesforce 401 — refreshing the access token and retrying")
                reauthed = True
                self._token = None
                self._ensure_auth()
                continue
            if (resp.status_code == 429 or resp.status_code >= 500) and attempt < _MAX_RETRIES:
                attempt += 1
                delay = _retry_after(resp, attempt)
                logger.warning("salesforce %s — retry %d/%d in %.1fs",
                               resp.status_code, attempt, _MAX_RETRIES, delay)
                time.sleep(delay)
                continue
            return resp

    def _send(self, method: str, url: str, *, params: dict | None = None,
              data: dict | None = None, json: dict | None = None,
              headers: dict | None = None) -> httpx.Response:
        if self._http is not None:
            return self._http.request(method, url, params=params, data=data,
                                      json=json, headers=headers)
        with httpx.Client(timeout=self._timeout) as client:
            return client.request(method, url, params=params, data=data,
                                  json=json, headers=headers)


def _retry_after(resp: httpx.Response, attempt: int) -> float:
    """The server's Retry-After when it sends one, else capped exponential
    backoff. Mirrors replyio_client._retry_after."""
    header = resp.headers.get("Retry-After")
    if header:
        try:
            return min(float(header), _BACKOFF_CAP_SECONDS)
        except ValueError:
            pass
    return min(2.0 ** attempt, _BACKOFF_CAP_SECONDS)
