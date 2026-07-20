# Scored Accounts: Complete Feature Guide

**Phase 1.5 of the ABM Intelligence Platform. The bridge between Discovery and outreach.**

In one line: Discovery tells us *who* is worth pursuing. Scored tells us *how good a fit*
each company is, *how confident* we are in that judgment, and *how to actually win* them.

Every company that reaches this screen gets a graded fit score on the rubric built for its
segment, a second independent quality check that can correct the first score, a recommended
play in plain English, a set of warm introduction paths through our own team, and a
one-click, board-ready research dossier. It turns a list of promising companies into a
ranked, sales-ready playbook.

*Audience: marketing and leadership. No technical background needed.*

---

## Contents

1. [Why it exists](#1-why-it-exists)
2. [Fit versus intent: two different questions](#2-fit-versus-intent)
3. [How a company gets here](#3-how-a-company-gets-here)
4. [The fit score and the three rubrics](#4-the-fit-score-and-the-three-rubrics)
5. [The three pillars every account reports](#5-the-three-pillars)
6. [Fit bands and tiers](#6-fit-bands-and-tiers)
7. [Independent QA: the built-in second opinion](#7-independent-qa)
8. [The Scored board](#8-the-scored-board)
9. [The spend meter and the Score-all batch](#9-the-spend-meter-and-the-score-all-batch)
10. [Filters and the fit legend](#10-filters-and-the-fit-legend)
11. [Anatomy of a scored row](#11-anatomy-of-a-scored-row)
12. [The account detail drawer](#12-the-account-detail-drawer)
13. [The recommendation: how to play it](#13-the-recommendation)
14. [Warm intros](#14-warm-intros)
15. [The Landing Page: a board-ready research dossier](#15-the-landing-page)
16. [Import, export, and reset](#16-import-export-and-reset)
17. [Cost controls](#17-cost-controls)
18. [Feature checklist](#18-feature-checklist)

---

## 1. Why it exists

Discovery answers one question: which companies are showing signs they might buy? That is
buying intent. But intent alone does not tell us whether a company is actually a good
customer for us, or what we should say when we reach out.

Scored answers the next three questions:

1. **How strong a fit is this company, really?** Not a gut call, a graded score on the
   rubric that matches the company's segment.
2. **Can we trust that score?** Every score is checked a second time by an independent pass
   that researches the facts again and can correct the first answer.
3. **What do we do about it?** A recommended angle, the warm paths into the account through
   our own network, and a complete research brief we can hand to a rep or an executive.

The result is that nobody has to manually research each company, argue about whether it is
a Tier 1 or a Tier 2, or start from a blank page when writing the first email. The system
does the grading, the fact-checking, and the first draft of the strategy.

---

## 2. Fit versus intent

These are two separate measurements, and keeping them separate is the whole point.

| | Intent (Discovery) | Fit (Scored) |
|--|--------------------|--------------|
| **Question it answers** | Is this company showing signs of buying *now*? | Is this company a *good customer* for us, regardless of timing? |
| **Scale** | 0 to 100, "Hot" or "Watch" | A segment rubric (for example 0 to 27 for a health system), "Tier 1" through "Tier 4" or "High / Medium / Low fit" |
| **Built from** | Live buying signals (new exec, hiring, funding, engagement) | Firmographics, technology stack, and business priorities, each researched and graded |
| **Changes when** | A new signal fires | Rarely, fit is structural; it is re-scored on demand |

A company can be high intent but low fit (it is hiring billers, but it is a 40,000-bed mega
system we do not sell to). It can be high fit but low intent (a perfect mid-market hospital
that is quiet right now). Scored exists to measure the fit axis properly, so we pursue
companies that are both a strong fit and in-market, in that priority order.

---

## 3. How a company gets here

Two roads lead into the Scored board, and they converge into one place:

| Road | How it works |
|------|--------------|
| **From Discovery** | A reviewer clicks "Score" on a qualified company in the Discovery panel. The company is promoted into the scoring queue with all of its discovery signals attached. |
| **From a CSV import** | A list of target accounts is uploaded directly (for example an existing ABM list or a list from a conference). These enter the queue too, so the team can score accounts that never came through Discovery. |

A small line at the bottom of the board states this plainly: "Scored accounts and CSV
imports converge here. QA runs independently on every score." Whichever road a company took,
it is graded the same way and gets the same independent QA check.

Inside the board, an account moves through a few states: **queued** (waiting to be scored),
**scoring** (in progress, shown live), **scored** (done, with a fit score), and **error**
(something failed, can be retried). The list sorts scoring first, then queued, then scored
by fit, so the most relevant rows are always at the top.

---

## 4. The fit score and the three rubrics

There is no single generic score. A hospital, a specialty practice, and an insurance plan
are completely different businesses, so each is graded on its own rubric. The system picks
the rubric automatically from the company's segment. Each rubric is a transparent point
system: every dimension has a written guide for how points are awarded, so a score is never
a black box.

### Health System rubric (27 points, 6 dimensions)

Built for community and mid-market hospital systems. The guiding principle is "small is
good": this rubric rewards systems around or below $2B in net patient revenue, because the
mega-enterprise systems are a different sales motion.

| Dimension | Max | How points are awarded |
|-----------|-----|------------------------|
| **Net Patient Revenue** | 10 | $1.0B to $2.0B = 10; $500M to $999M = 8; $200M to $499M = 6; under $200M = 4; $2.01B to $2.5B = 4; $2.51B to $3.5B = 2; over $3.5B = 0. A score of 0 here forces the whole account to the bottom tier (see auto-deprioritize below). Sub-$2B is the sweet spot. |
| **EMR Compatibility** | 5 | Any non-Epic system (Cerner, MEDITECH, Allscripts, athena, eClinicalWorks, NextGen) = 5; unknown or mixed = 3; Epic = 0. |
| **Competitor Landscape** | 4 | Uses a category buyer like Notable or AssortHealth = 4; uses general automation (UiPath, Automation Anywhere, Blue Prism) = 3; uses Palantir or custom AI = 2; no automation vendor found = 3; a direct revenue-cycle competitor already deployed = 0. |
| **Pain Point Signals** | 5 | One point each, up to 5: staffing shortages; rising costs or negative margins; rising claim or clinical denials; prior-authorization backlogs or manual workflows; multi-site billing complexity (expansion, mergers, multi-state). |
| **AI and Tech Readiness** | 2 | One point each: uses non-competing AI or publishes case studies; has a digital-transformation initiative, a recent innovation-leader hire, or a stated AI strategy. |
| **Leadership Changes** | 1 | A new CIO, CFO, COO, or CEO in the last 12 months = 1, else 0. |

**Auto-deprioritize:** a health system whose net patient revenue scores 0 (over $3.5B) is
forced to the bottom tier no matter how well it scores elsewhere, because it is simply too
large for our motion.

### Specialty rubric (30 points, 3 dimensions)

Built for specialty practices and physician groups: orthopedics, behavioral health,
physical therapy, ambulatory surgery centers, and similar.

| Dimension | Max | What it measures |
|-----------|-----|------------------|
| **Firmographic Fit** | 10 | Size, number of locations and providers, estimated revenue, growth indicators (expansion, hiring, funding), and how well the specialty fits. |
| **Technographic Fit** | 10 | The practice's EHR, practice-management and revenue-cycle systems, cloud versus legacy, digital adoption, known workflow gaps, and signs of upcoming modernization. |
| **Business Priorities and Intent** | 10 | Hiring patterns, leadership changes, new facilities, efficiency or margin mandates, press on AI or cost reduction, funding rounds, and private-equity backing. Recent signals (last 18 months) score highest; older signals alone cap this dimension at 6 out of 10. |

### Payer rubric (30 points, 3 dimensions)

Built for health plans and managed-care organizations. It requires at least 200,000 covered
lives and excludes the top-five national insurers (UnitedHealthcare, Elevance/Anthem,
CVS/Aetna, Cigna, Humana) unless a regional subsidiary is showing strong signals.

| Dimension | Max | What it measures |
|-----------|-----|------------------|
| **Firmographic** | 10 | Size, revenue, complexity, growth, estimated lives covered (200,000+), national versus regional scope, and plan type. |
| **Technographic** | 10 | Core administration platform, digital maturity, and integration needs. |
| **Intent** | 10 | Strength and recency (last 24 months) of automation signals: partnerships, pilots, executive hires, RFPs, conference talks, public statements. Weighted by pain points like prior-auth backlogs, claims-processing cost, member-services volume, and regulatory interoperability deadlines. |

Because each rubric is served to the dashboard from the same definition the scorer uses, the
score bars and tier badges you see can never drift from the logic that actually graded the
account.

---

## 5. The three pillars

However many dimensions a rubric has, every account rolls up into the same three pillars, so
any account is comparable at a glance regardless of segment:

| Pillar | What it captures |
|--------|------------------|
| **Firmographic** | Is the company the right size and shape? (For a health system, this is net patient revenue. For a specialty or payer, the firmographic dimension.) |
| **Technographic** | Is their technology a fit, and are they modernizing? (For a health system, this combines EMR compatibility and AI readiness.) |
| **Business Intent** | Are there real reasons to act now? (For a health system, this combines competitor landscape, pain points, and leadership changes.) |

The detail drawer and the Landing Page both show these three pillar scores at the top, so a
reader sees the shape of the fit (strong size, weak intent, for example) before reading any
detail.

---

## 6. Fit bands and tiers

The total points map to a named band, which drives the color and the badge on every screen.

| Rubric | Top band | Middle band | Lower band | Bottom band |
|--------|----------|-------------|------------|-------------|
| **Health System** (27 pts) | Tier 1, 22 to 27 (immediate ABM pursuit) | Tier 2, 16 to 21 (active targeted outreach) | Tier 3, 10 to 15 (monitor for triggers) | Tier 4, under 10 or net patient revenue over $3.5B (deprioritize) |
| **Specialty** (30 pts) | High Fit, 24+ | Medium Fit, 18 to 23 | Low Fit, under 18 | (none) |
| **Payer** (30 pts) | Tier 1, 22+ | Tier 2, 18 to 21 | Tier 3, under 18 | (none) |

The board groups everything into four plain buckets for the legend: **High**, **Medium**,
**Low**, and **Not a fit**, so leadership can read the distribution of the whole pipeline at
a glance without learning each rubric's tier names.

---

## 7. Independent QA

This is the trust layer, and it is the feature that makes the scores defensible.

Every single score is checked a second time by an independent pass, before anyone sees it.
The principle is "trust but verify." The QA reviewer is given the account, its known facts,
and the per-dimension scores the first analyst assigned, but deliberately **not** the first
analyst's reasoning or written summary. It cannot simply agree with a story it never saw.
It then researches the key verifiable facts again on its own (net patient revenue, the
EMR or revenue-cycle vendor, lives covered, organization size, recent signals) and decides
whether it agrees.

### The three verdicts

| Verdict | What it means |
|---------|---------------|
| **Verified** | QA independently checked the material facts and agrees with the score. No changes. |
| **Discrepancy** | QA found a material error and assigned a corrected score for that dimension. A discrepancy must carry an actual corrected number, never just an opinion. |
| **Unverifiable** | The evidence was too weak to confirm or correct, or QA could not complete. The score still ships, and a human is the backstop. QA is conservative on purpose: weak evidence becomes "unverifiable," not a false correction. |

### What happens when QA disagrees

When QA assigns corrected scores, those corrections become the **official** score, not just
a footnote. The system snapshots the original analyst score (so the drawer can show it
struck through), applies the corrected dimensions, recomputes the total, and re-resolves the
tier. The account is then marked "Adjusted by QA."

The loudest case is a **tier-changing discrepancy**: when applying QA's correction actually
moves the account into a different tier (for example a Tier 1 that QA pulls down to Tier 2
after finding the real net patient revenue). That is computed automatically, not left to
opinion, and the dashboard surfaces it prominently.

A few design choices worth noting for a non-technical reader:

- QA's effort scales with how much the account matters. A high-fit account, the kind shown
  to leadership, gets the full pass. A medium-fit account gets a focused check of just the
  two facts that move the tier most: revenue/size and the EMR vendor.
- Facts from a CSV import are treated as authoritative for firmographics. QA will only
  override them if it finds a cited public source that conflicts.
- QA can never break a score. If it fails for any reason, the verdict is simply
  "unverifiable" and the score still ships.

This is what lets us put a tier on an account in front of a CEO and stand behind it: it was
graded once, then independently fact-checked, and any correction is on the record.

---

## 8. The Scored board

The main screen, titled "Scored accounts: one fit score per account, on its segment rubric.
Open any row for the full breakdown."

![The Scored board](./images/scored_01_board.png)

The board is a single ranked table of every account that has been scored or is waiting to be
scored. Across the top are four actions:

| Action | What it does |
|--------|--------------|
| **Export** | Downloads the accounts in the current view (or just the ones you have selected) as a CSV, ready to hand to sales or load into a CRM. |
| **Find intros** | Kicks off the warm-intro search for every scored account that does not have one yet (see section 14). It previews the cost first. |
| **Reset** | Clears all scores back to the queue, so the team can re-run scoring selectively. It always asks for confirmation first. |
| **Import accounts** | Uploads a CSV of target accounts straight into the scoring queue. |

Export and Find intros only appear once there is something to act on, so the toolbar stays
clean when the board is empty.

---

## 9. The spend meter and the Score-all batch

Scoring uses paid research (each score, and its independent QA pass, use live web research),
so the board carries a live spend meter at the top, for example "Scoring spend this month
$13.83 / $200.00." It is part of a single shared budget across the platform and resets
monthly.

When there are queued accounts waiting, the meter doubles as the batch control:

- A **Score all** action scores the whole queue (or a chosen number) in one click.
- The batch is hard-capped to the monthly budget on the server, not just in the screen. If
  the budget is already reached, nothing is scored and the team is told. If only part of the
  batch fits the remaining budget, it scores what fits and leaves the rest queued.
- There is an overheat guard: if a running batch spends materially more than its estimate,
  it stops itself and reports how much it spent versus what it estimated.
- The board polls while anything is scoring, so rows and the meter update live and the final
  score lands without a manual refresh.

The net effect for leadership: scoring can never quietly run away with budget, and a single
person can grade a hundred accounts safely in one action.

---

## 10. Filters and the fit legend

The same table can be sliced to whatever the team cares about right now.

| Filter | Options |
|--------|---------|
| **Segment** | All, Health System, Specialty, Payer |
| **Fit** | All, High, Medium, Low, Not a fit |
| **Source** | All, Discovery, CSV import |
| **Date** | All time, Today, Last 7 days, Last 30 days |
| **Import** | Filter to a specific uploaded list, each shown with its account count |

On the right side of the filter bar, once there are scored accounts, a **fit legend** shows
the live distribution of the whole board, for example "High 8, Medium 78, Low 51, Not a fit
5." This is the one-glance health check on the pipeline: how many genuinely strong-fit
accounts do we have right now?

Rows can also be selected individually or all at once, which feeds the Export action so a
reviewer can hand-pick a shortlist to send out.

---

## 11. Anatomy of a scored row

Each row is one account and carries the headline of its grade.

| Element | What it tells you |
|---------|-------------------|
| **Selection box** | Tick it to include the account in a hand-picked export. |
| **Company name** | Clicking it opens the full detail drawer (section 12). |
| **Segment badge** | Health System, Specialty, or Payer, so you know which rubric graded it. |
| **ABM badge** | Whether the account matches our ABM target list (a confirmed match, or a name-only match to verify). |
| **Source and date** | "Discovery" or the import it came from, and when it was scored. |
| **Fit score ring** | The headline number on its rubric (for example 26 out of 27) with the band color, the single most important thing on the row. |
| **Action** | Queued accounts show a "Score" action; scored accounts open the drawer and the Landing Page. |

The ring color and the fit legend use the same four-band language (High, Medium, Low, Not a
fit), so scanning the column is instantly readable.

---

## 12. The account detail drawer

Click any account and a panel slides in with the entire case for it. This is the working
screen for deciding what to do with a company.

![Account drawer, top](./images/scored_02_drawer_top.png)

### Header and context

At the top: the company name and website, its segment badge, where it came from (Discovery
or an import), and which exact rubric and version scored it (for example "Health System
rubric, hs-2026.2"). A "Landing Page ready" tag links to the board-ready dossier. If the
account matches our ABM list, a callout shows the match (a confirmed match, or a name-only
match to verify).

### Why discovered

A "Why discovered" section lists the original buying signals that brought the company in (for
example several open revenue-cycle roles), each with a link to the proof. This keeps the
buying-intent story attached to the fit story, in one place.

### The score summary

The fit score ring (for example 26 of 27), the band (High, Tier 1), and, when QA changed
anything, an "Adjusted by QA" badge with the original analyst total shown struck through. A
single line records the provenance: the points out of the max, the number of dimensions, the
date it was scored, the model, and whether QA corrected it.

### Pillar scores

The three pillars (Firmographic, Technographic, Business Intent), each as a score out of its
maximum, so you see the shape of the fit immediately.

![Account drawer, middle](./images/scored_03_drawer_mid.png)

### The score breakdown and QA

Below the pillars, each individual dimension is broken out with its score and, where QA made
a correction, the correction is shown inline. The independent QA verdict (verified,
discrepancy, tier-changing, or unverifiable) is displayed as its own section, labeled as an
independent pass. For health systems the drawer also surfaces the competitor landscape it
found (for example "deployed UiPath robotic process automation for revenue cycle, no
direct revenue-cycle competitor found"), which is exactly the evidence behind the competitor
dimension.

### Known facts and origin

The firmographics the score was built on (segment, size, domain, and anything carried in
from an import or from Discovery), plus a link back to the original Discovery entry.

---

## 13. The recommendation

Near the bottom of the drawer is a plain-English recommendation titled "How to play it." It
is not a score, it is advice: where this account sits, why, and the angle to lead with.

![Account drawer, recommendation](./images/scored_04_drawer_bottom.png)

For example, for a strong Tier 1 health system the recommendation reads like a rep's opening
notes: it names the dollar figure that makes it a fit, the open roles that signal immediate
buying intent, the operational pain to lead with, and the specific wedge to position against
the technology they already run. This is the difference between handing sales a score and
handing them a starting move.

The action bar at the bottom of the drawer offers two buttons: **Open Landing Page** (the
full dossier, section 15) and **Re-score** (run the whole graded-plus-QA process again, for
example after new information).

---

## 14. Warm intros

A score tells us a company is worth pursuing. Warm intros tell us the *easiest way in*: a
person on our own team who already has a connection to the account.

For each scored account, the system can find the relevant decision-makers and then check
them against our team's network for a warm path. Three kinds of warm path are recognized:

| Path | What it means |
|------|---------------|
| **Engaged with Magical** | The contact has already interacted with us. |
| **Shared employer** | Someone on our team used to work at the same company. |
| **Shared school** | Someone on our team went to the same school. |

The footer of the section summarizes the result, for example "3 warm of 12, via Apollo and
LinkedIn, across 4 teammates," so a rep instantly knows whether there is a warm door to
knock on or whether it is a cold approach.

### Finding intros in bulk

The **Find intros** button on the board runs this for every scored account that needs it,
and previews the cost before starting:

- Decision-makers are found through Apollo, which is free, so every account gets a contact
  list at no cost.
- Strong-fit accounts (the High and Medium ones we are most likely to pursue) also get a
  deeper contact enrichment to surface warm paths. That enrichment has a small cost, roughly
  a few cents per account, and the button shows the total before you confirm.
- Lower-fit accounts keep their free contact list and never incur the paid step.

So the spend is concentrated exactly where it pays off: the accounts we actually intend to
chase.

---

## 15. The Landing Page

This is the deliverable. The "Open Landing Page" button produces a clean, board-ready
document for a single account: the kind of one-page brief you could hand to a rep before a
call or put in front of an executive.

![The Landing Page](./images/scored_05_landing_page.png)

The document always shows the account's fit scores (the three pillars and the overall score
with its band) and the recommendation, drawn straight from the score we already hold, at no
extra cost. Below that, it offers to generate a full research dossier on demand.

### The on-demand research dossier

The deeper research costs money (it uses live web research and contact lookup), so the
document shows a "Generate the full research dossier" call to action rather than spending
automatically. One click, roughly $0.70, generated once and then stored. The system is
honest about value: it notes that dossiers pay off most on High-fit accounts and gently says
so when an account is not High fit.

Once generated, the dossier expands into a complete account brief:

| Section | What it contains |
|---------|------------------|
| **Firmographic Profile** | The verified facts: size, locations, ownership, footprint. Each fact is marked as known, "likely," or "unknown / unconfirmed," so the reader knows how solid each line is. |
| **Services** | What the organization actually does. |
| **Business Intent Signals** | The buying signals, each with its own strength score out of 10. |
| **Decision Makers** | The relevant roles and contacts (found through Apollo), with LinkedIn links and notes. |
| **Entry Strategy** | The recommended way in: timing, the primary angles to lead with (ranked), cautions to avoid, and the likely deal size. |
| **RCM Complexity** | The revenue-cycle specifics that make this account a fit. |
| **Recent News and Context** | Recent, dated developments at the company. |
| **Key Pain Points** | The operational problems we can solve. |
| **Messaging Angles** | Ready-to-send lines a rep can use directly. |

The whole document can be downloaded as a PDF straight from the browser (the screen prints
just the document, not the app around it), and the footer stamps its provenance, for example
"Magical, Sonnet and Apollo, [date], Discovery." This is the artifact that turns a graded
account into something a salesperson can act on the same day.

---

## 16. Import, export, and reset

The board is not a closed system. Three controls let the team move accounts in and out.

| Control | What it does |
|---------|--------------|
| **Import accounts** | Upload a CSV of target accounts (an existing ABM list, a conference list, a partner list). They land in the scoring queue with their facts attached, and any firmographics from the CSV are treated as authoritative. Discovery accounts and imported accounts converge into the same board and are scored identically. |
| **Export** | Download the current view, or a hand-picked selection, as a CSV. The file name records what was exported (a segment, a source, an import, or "selected") and the date, so it is self-describing. |
| **Reset** | Clear all scores back to the queue, with confirmation, so scoring can be re-run from scratch or selectively. It will not run while a batch is in progress. |

This is what lets Scored serve both the inbound flow from Discovery and a deliberate,
team-curated target list, in one ranked place.

---

## 17. Cost controls

Scoring is the most research-intensive part of the platform (every account is graded and
then independently QA-checked, both using live web research), so cost is controlled at
several layers:

- A **live monthly spend meter** on the board, sharing one budget across the platform, that
  resets monthly.
- A **hard server-side cap**: batches stop at the budget even if the screen asks for more,
  and an overheat guard stops a batch that runs over its estimate.
- **Effort that scales with value**: the QA pass spends a full research budget on high-fit
  accounts and a focused, cheaper check on medium-fit ones.
- **Paid enrichment only where it pays off**: warm-intro enrichment and the research dossier
  are spent on strong-fit accounts, on demand, with the cost previewed first. Lower-fit
  accounts keep the free contact list and the free fit summary.

The team gets defensible, fact-checked scores and full research briefs, without the spend
ever being able to run away on its own.

---

## 18. Feature checklist

Everything the Scored platform does, in one place.

**Grading the fit**
- [x] One fit score per account, on the rubric built for its segment (no generic score)
- [x] Health System rubric: 27 points across 6 dimensions, net-patient-revenue-led, "small is good"
- [x] Specialty rubric: 30 points across firmographic, technographic, and intent
- [x] Payer rubric: 30 points across firmographic, technographic, and intent, 200k+ lives, top-5 nationals excluded
- [x] Transparent point system: every dimension has written scoring guidance
- [x] Three-pillar rollup (Firmographic, Technographic, Business Intent) on every account
- [x] Named fit bands and tiers, summarized as High / Medium / Low / Not a fit
- [x] Health-system auto-deprioritize when net patient revenue is over $3.5B
- [x] Recency rules: recent signals score highest, old signals are capped

**Trusting the score (independent QA)**
- [x] Every score independently re-checked by a second pass, before anyone sees it
- [x] QA sees the scores but not the first analyst's reasoning, so it cannot just agree
- [x] Three verdicts: verified, discrepancy, unverifiable
- [x] Corrections become the official score, with the original shown struck through
- [x] Tier-changing discrepancies computed automatically and surfaced loudly
- [x] QA effort scales with the account's fit; QA can never break a score
- [x] CSV firmographics treated as authoritative unless a cited source conflicts

**The board**
- [x] One ranked table of all scored and queued accounts, Discovery and imports converged
- [x] Account states: queued, scoring (live), scored, error
- [x] Filters: segment, fit band, source, date, and per-import list
- [x] Live fit-distribution legend across the whole board
- [x] Row selection and select-all for hand-picked exports

**Acting on the score**
- [x] Per-account detail drawer: header, why-discovered signals, score ring, pillars, dimension breakdown, QA verdict, known facts, and origin
- [x] Plain-English "how to play it" recommendation
- [x] Warm intros: decision-makers plus warm paths (engaged, shared employer, shared school) across the team
- [x] Bulk "Find intros" with cost preview, paid enrichment only on strong-fit accounts
- [x] Board-ready Landing Page with a fit summary, recommendation, and an on-demand research dossier
- [x] Dossier sections: firmographics, services, intent signals, decision makers, entry strategy, RCM complexity, recent news, pain points, messaging angles
- [x] Download any Landing Page as a PDF

**Moving accounts in and out**
- [x] Import a CSV of target accounts straight into the scoring queue
- [x] Export the current view or a selection as a self-describing CSV
- [x] Reset all scores back to the queue, with confirmation

**Cost**
- [x] Live monthly spend meter, shared budget, monthly reset
- [x] Hard server-side budget cap plus an overheat guard on batches
- [x] Score-all batch that respects the remaining budget
- [x] Paid research concentrated on the accounts worth pursuing

---

*Next guides: News, Watch list, and Engagement (Phase 2).*
