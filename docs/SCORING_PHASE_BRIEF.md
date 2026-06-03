# Scoring Phase — Design Brief

A self-contained brief for designing the next phase of the Magical ABM platform:
**account scoring**. It documents (1) what already exists and the design language to
match, (2) the scoring phase requirements, (3) a proposed architecture, data model,
and UX flow, and (4) open design questions. Hand this to a designer (human or
Claude Design) with no other context.

---

## 0. Product context

Magical sells agentic AI for revenue cycle management (RCM) to US healthcare
providers and payers. The platform finds and prioritizes target accounts.

Two stages:

1. **Discovery (built).** Buying signals (layoffs, leadership changes, M&A,
   funding, job postings) surface companies, which are qualified against the ICP
   by Claude and land in a review panel. A reviewer promotes, defers, or rejects.
2. **Scoring (this brief, not built).** A promoted or imported account is scored
   on a segment-specific rubric, independently QA'd, and presented on a scored
   dashboard with a per-dimension breakdown and a fit tier.

The user experience must feel like one continuous flow: a reviewer promotes a
company in Discovery and it transitions smoothly into the Scored view.

---

## 1. What exists today (and the design language to match)

### 1.1 Live system
- Discovery panel, deployed: a FastAPI service serving a static React UI.
- React 18 + Babel standalone (in-browser JSX), Tailwind via CDN. No build step.
- Files: `web/discovery/{index.html, api.js, ui.jsx, panel.jsx, app.jsx, drawer.jsx, autoscore.jsx}`.
- Backend: `auto_search/` (connectors, qualifier, pipeline, repos), Postgres or JSON repo behind one protocol, `auto_search/api/app.py`.

### 1.2 Visual language (match this exactly so scoring blends in)
- **Page:** background `#fafafa`, text `zinc-900`, max width `max-w-6xl`, generous padding.
- **Header:** sticky, translucent, backdrop blur. Left: indigo logo tile + "Magical / Discovery" breadcrumb. Right: live status text, an Auto-score pill, a Refresh button.
- **Primary color:** `indigo-600` (buttons, active states, accents).
- **Neutrals:** `zinc-50/100/200/400/500/900`. Cards are white, `rounded-2xl`, hairline `zinc-200` border, very soft shadow.
- **State colors:** emerald = qualified / positive / hiring; amber = needs review / caution; rose = reject / error; sky = leadership; amber = acquisition; cyan = funding; indigo = primary actions.
- **Segments:** health_system = blue, specialty = teal, payer = violet (pill with a dot).
- **Type scale:** titles ~24px semibold; section labels 11px uppercase tracked zinc-400; body 13-14px; meta 12px zinc-400.

### 1.3 Layout pattern
- Stat tiles row: `grid-cols-2 sm:grid-cols-4`, each tile a big number + label, the active one tinted indigo.
- Main card: tab row (border-bottom, count chips) → filter row (labelled dropdowns, right-aligned count) → list of rows.
- **Rows:** identity (name + segment badge) on the left, signal chips beneath, meta line (counts, "seen 3d ago", staff) under that; a confidence meter in the middle; actions on the right (hover-revealed secondary actions + a primary button + a chevron). Rows collapse out with a left-slide + max-height animation when actioned.
- **Drawer:** right-side slide-over (`fixed inset-y-0 right-0`), ~`max-w-md`, for detail. Header with name + external domain link; a verdict block (confidence + reasoning + "View evidence"); a "Why discovered" timeline (icon-dotted) and, for job postings, a role-grouped "Open RCM roles" block with linked openings; a bottom action bar.

### 1.4 Component inventory (already built, reuse or mirror)
`StatTile`, `TabButton`, `SegmentBadge`, `ConfidenceMeter`, `SignalChip`,
`JobRoleChip` (count-led, e.g. "3 Coder jobs"), `SignalChips` (groups job
postings by role), `CompanyRow`, `CompanyDrawer`, `SectionLabel`,
`PromoteButton` / `DeferButton` / `RejectButton` / `RestoreButton`,
`Dropdown`, `RejectReasonModal`, `EmptyState`, `ToastStack` (2.6s toasts),
`ActivityFeed` (fading bottom-left per-account ticker), `ActivityBanner`
(top "Discovering…" strip), `Icons` (inline SVG set), `SIGNAL_META`,
`SEGMENT_META`, `DECISION_META`. Helpers: `relativeTime`, `shortDate`.

### 1.5 Interaction patterns to reuse
- Optimistic action then collapse-out animation (promote/defer/reject).
- Toast on every action.
- Live polling (`/api/activity` every 4s) drives a top banner while a run is in
  progress and a fading corner ticker of per-account decisions.
- Confidence shown as a thin two-tone meter bar + percent.

### 1.6 Current workflow + storage
- `review_status`: pending | promoted | rejected | deferred (human decision),
  separate from `icp_status`: qualified | needs_review | disqualified | error
  (machine verdict). Tabs: Qualified, Needs review, Deferred.
- **Promote today is a stub:** it sets `review_status='promoted'` and returns a
  placeholder account id. It does NOT yet create a real account or run scoring.
  That stub is the seam the scoring phase plugs into.

---

## 2. Scoring phase — requirements

### 2.1 Two inputs, one queue
1. **Promote from Discovery.** Promoting a qualified company creates an Account
   and enqueues it for scoring. Its discovery signals (job postings, layoffs,
   leadership, funding) and ICP verdict carry over as known intent context.
2. **CSV import (manual, bulk).** Upload a Definitive-Healthcare-style list;
   each row becomes an Account and is enqueued. CSV columns provide structured
   firmographic and technographic facts that pre-fill the rubric.

Both converge into the same Accounts table and scoring queue.

### 2.2 Three segment-specific frameworks
The rubric is chosen by segment. Each dimension has a score, a max, and a written
rationale ("why"). The UI must render an arbitrary number of dimensions generically.

**A. Specialty / Physician Group — 30 points**
- Firmographic Fit (0-10): size, locations/providers, revenue, growth, specialty fit.
- Technographic Fit (0-10): EHR/PM/RCM systems, cloud vs legacy, digital adoption, workflow gaps, modernization signals.
- Business Priorities & Intent (0-10): RCM/ops/IT hiring, leadership changes, expansions, efficiency/margin mandates, AI/cost press, funding, PE-backed.
- Tiers: 24-30 High Fit, 18-23 Medium Fit, <18 Low Fit. Plus a 1-paragraph recommendation.

**B. Payer — 30 points**
- Firmographic (1-10): size, revenue, complexity, growth; lives covered (200k+), nationwide scope.
- Technographic (1-10): stack, digital maturity, integration needs; core admin platform.
- Intent (1-10): strength + recency of AI-automation signals (partnerships, pilots, exec hires, RFPs, conference talks) in last 24 months; pain points (prior auth backlogs, claims cost, member-services volume, CMS interoperability deadlines).
- Tiers: Tier 1 = 22+, Tier 2 = 18-21, Tier 3 = 15-17. Exclude top-5 nationals unless a regional subsidiary shows strong signals.

**C. Health System — 27 points (six dimensions)**
- Net Patient Revenue (10): $1.0-2.0B = 10; $500-999M = 8; $200-499M = 6; <$200M = 4; $2.01-2.5B = 4; $2.51-3.5B = 2; >$3.5B = 0 and auto Tier 4. ("Small is good", ICP modeled on Beacon Health, prioritize ≤$2B.)
- EMR Compatibility (5): any non-Epic = 5; unknown/mixed = 3; Epic = 0.
- Competitor Landscape (4): Notable/AssortHealth = 4; UiPath/Automation Anywhere/Blue Prism = 3; Palantir/custom AI = 2; none found = 3; ThoughtfulAI or direct RCM competitor = 0.
- Pain Point Signals (5, +1 each): staffing shortages; rising costs/negative margins; denials up; prior-auth backlogs/manual workflows; multi-site billing complexity.
- AI & Tech Readiness (2, +1 each): uses non-competing AI / publishes case studies; has a digital-transformation initiative or new CDO/VP Innovation or stated AI strategy.
- Leadership Changes (1): new CIO/CFO/COO/CEO in last 12 months.

> The exact prompt text for each framework lives with the user; the dimensions
> and tier bands above are what the UI must visualize.

### 2.3 Independent QA agent
A second, independent Claude pass verifies the score. It does NOT see the
scorer's reasoning; it gets the account (and any provided CSV facts) and the
claimed dimension scores, then independently checks the key facts (NPR, EMR/RCM
vendor, size, lives covered, recent signals). It returns:
- `status`: verified | discrepancy | unverifiable
- `notes`: short explanation
- `corrections`: list of {dimension, claimed, found} where it disagrees
A discrepancy that would change the tier is highlighted. QA status shows as a
badge on every scored account; the breakdown shows per-correction detail.

### 2.4 Output: scored dashboard with expansion
- A list of scored accounts, each showing: name, segment badge, **total score**
  (e.g. 24/30) as a ring or prominent number, **tier badge** (High Fit / Tier 1,
  color-coded by band), source (Discovery vs CSV), and a **QA badge**.
- **Click to expand** into a per-dimension breakdown: each dimension as a row
  with its label, a score bar (score/max), the written "why", any "inferred" or
  "unknown" flags, and the QA note/correction if any. Plus the 1-paragraph
  recommendation and a link back to the originating discovery signals.
- Filters: segment, tier, source, QA status. Stat tiles: total scored, High Fit /
  Tier 1 count, pending QA, average score.

---

## 3. Proposed architecture

### 3.1 Backend
- `frameworks/` config: each framework = { key, label, dimensions[{key,label,max}], tier_bands, prompt_template }.
- `scoring/engine.py`: pick framework by segment → build prompt with known facts
  (CSV columns + discovery signals injected so Claude does not re-infer given
  data) → one Sonnet call (web_search on) → parse structured JSON → persist.
- `scoring/qa.py`: independent Sonnet pass (no scorer reasoning) → verdict.
- `imports/csv.py`: detect schema, map columns to a normalized Account +
  firmographic/tech facts, dedupe (domain-first, then name), enqueue.
- A scoring queue/worker so the UI can show "scoring…" then resolve (reuse the
  activity-feed pattern: a `/api/scoring/activity` endpoint).
- All models Sonnet (`claude-sonnet-4-5`). No Opus in this phase. Enterprise key
  removes the rate-limit constraint.

### 3.2 Data model (new tables)
- `accounts`: id, source (discovery|csv), discovery_company_key (nullable),
  domain, name, segment, firmographics JSONB (normalized from CSV/enrichment),
  created_at.
- `scores`: id, account_id, framework, framework_version, dimensions JSONB
  ([{key,label,score,max,summary,evidence,flags}]), total, max_total, tier_label,
  tier_band, recommendation, model, cost_usd, scored_at.
- `score_qa`: id, score_id, status, notes, corrections JSONB, confidence, qa_at.

### 3.3 Score result shape (what the UI renders)
```json
{
  "account": { "name": "...", "segment": "health_system", "domain": "...", "source": "csv" },
  "framework": "health_system",
  "dimensions": [
    { "key": "npr", "label": "Net Patient Revenue", "score": 10, "max": 10,
      "summary": "NPR ~$1.4B (Definitive).", "flags": [] },
    { "key": "emr", "label": "EMR Compatibility", "score": 5, "max": 5,
      "summary": "MEDITECH inpatient, non-Epic.", "flags": [] }
  ],
  "total": 22, "max_total": 27,
  "tier": { "label": "Tier 1", "band": "high" },
  "recommendation": "Strong fit: sub-$2B, non-Epic, active denials pressure ...",
  "qa": { "status": "verified", "notes": "NPR and EMR match Definitive.",
          "corrections": [] }
}
```

### 3.4 Cost
- ~1 scoring call + 1 QA call per account, Sonnet. Roughly $0.08-0.15 per account
  when CSV facts reduce research. (Discovery reference: ~$0.075 per company.)
  Show cost per account in an admin/debug view, not the main UI.

---

## 4. Proposed UX flow (for the designer to refine)

### 4.1 Navigation
Recommendation: a top-level switch in the header breadcrumb, **Discovery | Scored**,
sharing the same shell (header, stat tiles, card, drawer). Scoring is a distinct
stage, so a sibling view reads more clearly than a fourth tab. (Alternative: a
"Scored" tab beside Qualified / Needs review / Deferred.)

### 4.2 The promote-to-scored transition (must feel seamless)
1. Reviewer clicks Promote on a qualified company.
2. It collapses out of the Qualified list (existing animation) and a toast says
   "Scoring {name}…".
3. In the Scored view it appears immediately as a card in a "scoring…" shimmer
   state (skeleton score ring), optionally with the corner ticker noting it.
4. When the score + QA resolve, the card fills in with the total, tier, and QA
   badge (gentle reveal). No manual refresh.

### 4.3 Scored dashboard
- Stat tiles: Scored, High Fit / Tier 1, Pending QA, Avg score.
- Filter row: Segment, Tier, Source, QA status. "Import accounts" button (right).
- Score cards/rows: name + segment badge | total score ring (score/max) | tier
  badge | QA badge | chevron. Sort by score desc by default.
- Expand (drawer or inline accordion): dimension rows (label, score bar, why,
  flags, QA correction), recommendation paragraph, link to discovery signals,
  actions (e.g. add to list, re-score, mark reviewed).

### 4.4 CSV import flow
1. "Import accounts" → drop a CSV.
2. Auto-detect the Definitive schema; show a column-mapping step (mapped vs
   unmapped, with the ability to set the segment if not inferable from "Firm Type").
3. Preview the first rows + dedupe summary (new vs already-known).
4. Import → bulk scoring with a progress indicator (reuse the activity feed):
   cards stream in as each row is scored.

### 4.5 Tier and score visualization
- Total score as a radial ring or a bold number over max, colored by tier band
  (high = emerald/indigo, medium = amber, low = zinc).
- Tier badge mirrors the existing `SegmentBadge` pill style.
- Dimension score bars mirror the existing `ConfidenceMeter`.
- QA badge: emerald check = verified, amber = discrepancy, zinc = unverifiable.

---

## 5. CSV schemas (the two provided examples)

**Health Systems** (`...Health Systems.csv`): Hospital Name, Firm Type, Hospital
Type, Epic?, NPR ICP?, Health System, Independent Hospital?, EHR Inpatient, EHR
Ambulatory, Population Health Management, **Revenue Cycle Management**, Medicare
Discharges, Address, City, State, Definitive ID, Provider Number, IDN, IDN Parent,
**Net Patient Revenue**, # of Discharges, # of Staffed Beds.
→ Pre-fills HS dimensions: NPR (10), EMR (5, from Epic?/EHR Inpatient), and gives
the RCM vendor for the competitor dimension.

**Physician Groups** (`...PGs - Anesthesiology.csv`): Physician Group Name, Firm
Type, Website, # of Locations, # of Physicians, # of Group Practice Members,
Ambulatory EMR, ICP Specialty, Ortho?, Behavioral Health?, Main/Other Specialties,
Medicare Allowed/Charges/Pmts, Hospital/ACO/MSO/GPO/IPA affiliations, City, State,
CEO/CFO/COO names+titles+emails, Definitive ID, DHC Profile Link.
→ Pre-fills Specialty firmographic (size, locations, providers, specialty,
revenue proxy via Medicare) and technographic (Ambulatory EMR); leadership names
support the intent dimension.

> Treat the schema as variable: map by header name with sensible fallbacks, and
> let the user confirm the mapping and segment at import time.

---

## 6. Open design questions

1. **Scored as a sibling view (Discovery | Scored) or a fourth tab?** (Lean: sibling view.)
2. **Auto-score on promote, or a manual "Score" action?** The header already has an
   Auto-score concept. Recommended: promote auto-enqueues, with the option to turn it off.
3. **Framework selection for CSV rows:** infer from "Firm Type"/file, or ask at import.
4. **What does QA gate?** Recommended: QA flags only; the human decides. A
   tier-changing discrepancy should be visually loud.
5. **Re-scoring + framework versions:** show which framework version produced a
   score; allow re-score when a rubric changes.
6. **What happens after scored?** The terminal action (add to a targeted list,
   export, sync to CRM) — out of scope for this brief but worth a placeholder in
   the UI (e.g. an "Add to list" action on a scored account).

---

## 7. What to hand the designer

- This brief.
- The live URL + login (so the current UI can be explored directly).
- The actual UI source: `web/discovery/{ui.jsx, panel.jsx, app.jsx, drawer.jsx, index.html}`
  — the ground truth for the component library and visual language.
- Screenshots of the panel and the company drawer.
- The three scoring prompts (full text) and the two CSV samples.
