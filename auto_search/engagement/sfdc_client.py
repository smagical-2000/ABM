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
from collections.abc import Iterator

import httpx

logger = logging.getLogger(__name__)

_API_VERSION = "v60.0"
_PAGE_CAP = 500          # hard stop so a bad nextRecordsUrl can't loop forever
# Floor for the high-intent lead pull — leads older than this are stale; we only
# track the 2026-onward cohort (per the user). Open-ended (since -> now).
SINCE_DEFAULT = "2026-01-01"
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


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
        READ ONLY — callers must pass a SELECT."""
        self._ensure_auth()
        path = f"/services/data/{self._api}/query"
        params: dict | None = {"q": soql}
        for _ in range(_PAGE_CAP):
            resp = self._send("GET", f"{self._instance}{path}", params=params,
                              headers={"Authorization": f"Bearer {self._token}"})
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
        sources = ", ".join("'" + s.replace("'", r"\'") + "'"
                            for s in self.HIGH_INTENT_SOURCES)
        yield from self.query(
            "SELECT Id, FirstName, LastName, Company, Email, BN_Email_Domain__c, "
            "Website, Title, LeadSource, Status, Rating, MQL__c, Seats_Requested__c, "
            "In_Healthcare__c, Primary_Purpose__c, Employee_Range__c, IsConverted, "
            "CreatedDate FROM Lead "
            f"WHERE LeadSource IN ({sources}) AND CreatedDate >= {since}T00:00:00Z "
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
            "WHERE LeadSource LIKE '%| TOFU' "
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
        safe = e.replace("\\", r"\\").replace("'", r"\'")
        return next(self.query(
            f"SELECT Id FROM Lead WHERE Email = '{safe}' LIMIT 1"), None) is not None

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

    def _send(self, method: str, url: str, *, params: dict | None = None,
              data: dict | None = None, json: dict | None = None,
              headers: dict | None = None) -> httpx.Response:
        if self._http is not None:
            return self._http.request(method, url, params=params, data=data,
                                      json=json, headers=headers)
        with httpx.Client(timeout=self._timeout) as client:
            return client.request(method, url, params=params, data=data,
                                  json=json, headers=headers)
