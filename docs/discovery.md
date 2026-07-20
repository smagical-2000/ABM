# Discovery Platform: Complete Feature Guide

**Phase 1 of the ABM Intelligence Platform.**

In one line: Discovery automatically finds healthcare companies that look like our ideal
customer and are showing signs they are about to buy, then ranks them so the team knows
exactly who is worth pursuing.

Think of it as an always-on research analyst. Instead of a person manually combing job
boards and news for accounts, the system watches the market every weekday, spots the
buying signals, checks each company against who we actually sell to, scores how strong the
intent is, and hands us a ranked shortlist with the evidence behind every entry.

*Audience: marketing and leadership. No technical background needed.*

---

## Contents

1. [Why it exists](#1-why-it-exists)
2. [How a company travels through Discovery (the funnel)](#2-the-funnel)
3. [The signals we track](#3-the-signals-we-track)
4. [Who qualifies and who does not (the ICP)](#4-the-icp)
5. [The stacking and parking rule](#5-the-stacking-and-parking-rule)
6. [How a company gets its intent score](#6-intent-scoring)
7. [The lifecycle: how a company moves between Watch, Review, Qualified, and Rejected (TTL)](#7-the-lifecycle-ttl)
8. [The top navigation](#8-the-top-navigation)
9. [The Discovery Panel and its status cards](#9-the-discovery-panel)
10. [Filters and search](#10-filters-and-search)
11. [Anatomy of a company row](#11-anatomy-of-a-company-row)
12. [The company detail panel](#12-the-company-detail-panel)
13. [The Needs-review queue](#13-the-needs-review-queue)
14. [Cost controls](#14-cost-controls)
15. [Feature checklist](#15-feature-checklist)

---

## 1. Why it exists

Our sales motion is account-based, meaning we focus on a specific list of companies we
want as customers. Two problems with relying only on that static list:

1. It is finite. Great-fit companies show buying signals every week that are not on the
   list yet, for example a hospital that just hired a new VP of Revenue Cycle, a practice
   group posting a wave of billing jobs, or a payer that just raised funding.
2. Tracking those signals by hand does not scale. Someone would have to watch job boards,
   news, and LinkedIn every day, judge each company, and keep a spreadsheet current.

Discovery closes both gaps. It surfaces the right companies automatically, with the
evidence attached, so the pipeline grows beyond the static target list and nobody spends
their day combing sources by hand.

---

## 2. The funnel

Every company that appears in Discovery passed through a three-layer funnel. Each layer is
cheaper than the next, so we only spend money researching companies that already look
promising.

| Layer | What it does | Cost |
|-------|--------------|------|
| **1. Keyword gate** | The market scan pulls candidates by searching for revenue-cycle job titles and other buying signals. | Free |
| **2. Job qualifier** | Reads each job posting's title and description and keeps only genuine hands-on revenue-cycle roles, throwing out look-alikes (a "Coding Instructor", an "RCM Software Engineer", a "Billing Sales Rep"). | A few cents per run |
| **3. Company / ICP qualifier** | For the survivors, researches the company's real website and decides whether the company itself fits our ideal customer profile. | Higher (uses live web research), so it only runs on companies that cleared layers 1 and 2 |

The point of the funnel: we never spend an expensive company research call on a company
whose only "RCM" posting turned out to be a software-engineer or trainer role.

---

## 3. The signals we track

A "signal" is any event that suggests a company may be entering the market for
revenue-cycle help. Discovery currently tracks seven signal types:

| Signal | What it is | Why it matters |
|--------|-----------|----------------|
| **Job posting** | An open revenue-cycle role (billing, coding, AR / collections, denials, prior authorization, eligibility, charge capture, patient access). | Hiring this work means the company is scaling or struggling with it. |
| **Leadership change** | A new executive, for example a new CFO, CMO, or a revenue-cycle leader hire. | A new leader is a fresh buying window. They reassess vendors and tools. |
| **Executive engaged** | An executive at the company interacted with Magical (for example on LinkedIn). | The strongest soft signal: they already know us. |
| **Layoff** | The company announced layoffs. | Cost pressure makes an efficiency / automation play timely. |
| **Acquisition** | The company acquired or merged with another. | Integration drives operations scaling. |
| **Funding round** | The company raised funding. | New budget and growth pressure. |
| **Event attendance** | The company showed up at a relevant industry event. | An in-market indicator. |

Job postings are the highest-volume source, so they get an extra filter (see the job
qualifier in section 2, and the stacking rule in section 5).

---

## 4. The ICP

The company qualifier researches each company's actual website and judges whether the
company fits Magical's Ideal Customer Profile. It judges the company, not the signal, so
a staffing agency hiring a biller is correctly rejected even though the job title matched.

### Qualifies (one of three segments)

| Segment | Definition |
|---------|-----------|
| **Specialty practice** | Orthopedics, behavioral health, physical therapy, or ambulatory surgery centers. Multi-location group, roughly 100 to 5,000 employees, US-based. Sub-types: ortho, behavioral health, PT, ASC. |
| **Payer** | Medicare Advantage MCO, Medicaid MCO, or a regional Blue Cross plan. Roughly 500+ employees, US-based. Sub-types: Medicare Advantage, Medicaid MCO, BCBS. |
| **Health system** | Community or mid-market hospital system, roughly 1,000 to 50,000 employees, US-based. Sub-types: community hospital, mid-market health system. |

### Hard disqualifiers (any one rules the company out)

Headquartered outside the US, pure tech / SaaS / digital-health vendor, pharma
manufacturer, medical device manufacturer, biotech, dental-only practice, solo or small
clinic under 100 employees, mega-enterprise health system (Mayo, Kaiser, HCA, Ascension,
Cleveland Clinic, Providence, which are a different sales motion), government agency (VA,
IHS, state health department), health-insurance broker or agency, consumer wellness app,
and lab-testing-only companies (LabCorp, Quest).

### What the qualifier records for each company

Qualified yes or no, segment, sub-segment, company type (provider, payer, vendor, tech,
pharma, device, biotech, government, consumer, other), approximate employee count, a
confidence score (0.0 to 1.0), a 2 to 3 sentence reasoning with evidence from the website,
the evidence URL, and the company domain. When confidence is below 0.7 or the website
could not be read, the company is flagged **needs human review** rather than auto-decided.

---

## 5. The stacking and parking rule

Job postings are noisy, so Discovery applies one more cost-shaping rule before paying for
the expensive company research. Every open role is tagged as one of two tiers:

- **Core roles**: prior authorization, denials / appeals, eligibility, claims, revenue
  cycle / integrity, utilization review. This is the high-intent work Magical automates
  directly. A single open core role is a buying signal on its own.
- **Standard roles**: billers, coders, patient access, scheduling. Higher-volume and
  noisier. A single open standard role is often just routine backfill.

The decision per company:

| Situation | Action |
|-----------|--------|
| Any non-job signal present (leadership, funding, social, etc.) | **Qualify** (research it now) |
| At least one core role open | **Qualify** |
| Two or more standard roles open (a real build-out) | **Qualify** |
| Only a single standard role and nothing else | **Park** (watch it, spend nothing) |

A parked company costs nothing to hold. It is re-checked on every run, so the moment it
opens a second revenue-cycle role it qualifies automatically on the next pass. This is the
mechanic behind the in-product line "Watching N companies with a single open RCM role,
they auto-qualify the moment a second role opens." A parked company that stops being seen
for 30 days is pruned from the watch ledger.

---

## 6. Intent scoring

Once a company is qualified, Discovery scores how strong its buying intent is, from 0 to
100. This is deliberately transparent: a clear point system, not an AI black box. Anyone
can see exactly why a company scored what it did, because every component adds a short
written reason (for example "New exec, 4 roles open, ABM target").

![How intent is scored](./images/disc_02_intent_scored.png)

### Base points (set by the single strongest signal)

| Strongest signal | Points |
|------------------|--------|
| New executive (leadership change) | 65 |
| Executive engaged with us | 60 |
| Layoff | 50 |
| Acquisition | 50 |
| Funding round | 50 |
| Revenue-cycle leader hire (job) | 50 |
| Event attendance | 45 |
| Core revenue-cycle role (job) | 30 |
| Standard RCM role (job) | 18 |

### Bonus points (added on top of the base)

| Bonus | Points |
|-------|--------|
| Each extra open role (stacking) | +15 each, capped at +45 |
| Two or more different signal types (multi-signal) | +20 |
| Company is a confirmed ABM target | +20 |
| A signal landed in the last 7 days (fresh) | +5 |
| The account is already engaging with us (Warm) | +1 |
| The account is already engaging with us (Hot) | +2 |

The final score is capped at 100.

### The threshold

**65 or above is Hot.** Hot companies are strong enough to research automatically (if the
Auto-score toggle is on). Below 65 is Watch, held until the company heats up. The line is
drawn visually across the panel as the "Auto-score line": above it is scored
automatically, below it is watched.

(The in-product popover shows a simplified version of this table. The full set of base
weights above is the exact logic the system uses.)

---

## 7. The lifecycle (TTL)

A list of leads is only useful if it stays honest. Discovery has a self-cleaning lifecycle
that ages out leads that go cold and re-promotes leads that heat back up, so nothing rots
silently in the queue. This runs as a daily sweep.

There are two separate clocks, on purpose:

| Clock | Trigger | What happens |
|-------|---------|--------------|
| **Watch TTL (7 days, by signal age)** | A qualified lead has had no fresh signal for 7 days and is no longer Hot. | It drops from Qualified to **Needs review**. |
| **Review TTL (7 days, by time in review)** | A Needs-review lead has sat in review for more than 7 days and is still cold. | It is **auto-rejected** (and can be restored anytime). |

Why two clocks: the Watch clock keys off how old the signals are (a qualified lead that
went quiet). The Review clock keys off how long the lead has been waiting for a human
(entered_review time), so a lead with a perpetually-fresh-but-weak signal still ages out
if nobody acts on it.

### Re-heating (the lead comes back to life)

If a Needs-review lead re-heats to Hot, what happens depends on why it was in review:

- **Cooled from Watch (origin: decayed)**: it was qualified before and merely went cold.
  Re-heating to Hot promotes it straight back to Qualified.
- **AI unsure (origin: ingest)**: it is in review because the AI could not confidently
  qualify it in the first place. Buying intent does not answer the "is this even a fit"
  question, so it is never auto-promoted. It is surfaced as Hot and sorted to the top for
  a human to decide.

A Hot lead is never auto-rejected. Auto-rejected leads are recoverable: an aged-out lead
restores exactly like a manually rejected one, so nothing is ever lost.

### The full flow

```
   Market scan
        |
        v
   Keyword gate (free)  ->  Job qualifier (cheap)  ->  Company / ICP qualifier (web research)
                                                              |
                          +-----------------------------------+-----------------------------------+
                          v                                   v                                   v
                     QUALIFIED                           NEEDS REVIEW                        DISQUALIFIED
                  (a real fit + signal)             (AI not confident, or               (not our kind of
                          |                          a single standard role)             company, hidden)
                          |                                   |
              no fresh signal 7 days                 in review 7 days, still cold
                          |                                   |
                          v                                   v
                   NEEDS REVIEW  <--- re-heats to Hot --- (decayed origin promotes back)
                                                            else AUTO-REJECTED (restorable)
```

Only "pending" leads move automatically. A lead a person has already scored or deferred is
never touched by the sweep.

---

## 8. The top navigation

The bar at the top of every screen.

![Discovery Panel](./images/disc_01_panel.png)

| Item | What it does |
|------|--------------|
| **Discovery / Scored / News / Watch list / Engagement** | The five areas of the platform. This guide covers Discovery. The others have their own guides. |
| **"Live, N surfaced"** | A running count of how many companies the system has surfaced to date. Evidence it is working continuously. |
| **Auto-score toggle** | When on, the hottest companies are researched automatically with no human in the loop. When off (the default), a person approves each one. |
| **Scan signals** | Runs a fresh market scan on demand. It also runs automatically every weekday. |
| **Refresh** | Reloads the panel with the latest results. |

---

## 9. The Discovery Panel

The main screen, titled "Discovery Panel: review AI-qualified companies and route each one.
Score or reject." This is where a person reviews what the system found and decides what to
pursue.

### The four status cards

The funnel at a glance, left to right:

| Card | What it means | Example |
|------|---------------|---------|
| **In queue** | New companies the system qualified, waiting for a person to review. | 23 |
| **Qualified** | All companies that passed the bar (right fit plus a real signal). | 115 |
| **Needs review** | Borderline companies held for a closer look. | 124 |
| **Disqualified (hidden)** | Companies the system judged not a fit and filtered out, so the team never wades through noise. | 550 |

The takeaway: the system did the filtering. 550 companies were ruled out automatically so
the team only looks at the roughly 100-plus that matter.

### The two tabs

- **Qualified**: companies confident enough to act on.
- **Needs review**: borderline companies (see section 13).

---

## 10. Filters and search

Slice the list to whatever the team cares about right now.

| Filter | Options |
|--------|---------|
| **Segment** | All, Health System, Specialty, Payer |
| **Signal** | All, Hiring, Layoff, Leadership change, Acquisition, Funding, Engaged, Event |
| **ABM list** | All, On ABM list, ABM confirmed |

Other controls on this bar:

- **"How intent is scored"**: opens the scoring breakdown (section 6).
- **ABM cross-reference**: the panel constantly checks finds against the 2,687-company ABM
  target list and shows how many of the current view are already targets versus brand-new
  discoveries, for example "23 companies, 9 on ABM list".
- **Select all**: lets a person act on many companies at once (bulk routing).

---

## 11. Anatomy of a company row

Each row is one company and carries the full story of why it surfaced.

| Element | What it tells you |
|---------|-------------------|
| **Company name** | The organization. Clicking it opens the detail panel (section 12). |
| **Segment badge** | Health System, Specialty, or Payer. |
| **ABM badge** | "ABM target" (a confirmed match to the target list) or "ABM?" (a name-only match to verify). Hover shows the matched target name and location. |
| **Review-origin tag** | On Needs-review rows only: "Cooled from Watch" (was qualified, then went cold) or "AI unsure" (the AI could not confidently qualify or disqualify it). |
| **Signal chips** | The buying signals on the company, for example "2 RCM roles open" or "1 Revenue Cycle job". |
| **TTL hint** | When the lead will auto-move, so nothing rots silently: "To review in Nd" (a Watch lead whose signals are going stale) or "Auto-rejects in Nd" (a Needs-review lead aging out). Turns amber when it is two days or less. |
| **Date and size** | When the signal fired and the approximate employee count. |
| **Intent meter** | A fill bar plus the 0 to 100 score plus the tier pill (Hot or Watch). |
| **Action buttons** | Score (promote to deep research), Defer (hold for later), Reject (remove). Rejected rows show Restore instead. |

A standing note above the list flags the most common holding pattern, for example
"Watching 67 companies with a single open RCM role, they auto-qualify the moment a second
role opens."

The **Auto-score line** is a divider across the list: companies above it are Hot
(auto-scored), companies below it are Watch (held).

---

## 12. The company detail panel

Click any company and a panel slides in with the full case for it. This is the screen a
person uses to make the call on each company.

![Company detail panel](./images/disc_04_company_drawer.png)

| Part | What it shows |
|------|---------------|
| **Header** | Company name, a link to its website, and a close button. |
| **Tags** | Segment plus descriptors, for example "Specialty, behavioral health, provider". |
| **ABM banner** | "On your ABM target list, [name], Matches" when the company is a confirmed target, or "Possible ABM-list match" (name only, verify) otherwise. |
| **ICP confidence** | A 0 to 100 percent score (AI-assessed) for how well the company fits our ideal customer, for example 95 percent. |
| **Why qualified** | A plain-English paragraph explaining the fit: what the company does, its size and footprint, and which segment it matches. The AI's reasoning is shown, not hidden. |
| **View evidence** | Opens the underlying source signals, the actual job postings or news that triggered the surfacing. |
| **Firmographics** | Segment, sub-segment, employee count, domain. |
| **Signals** | The buying signals on this company. |
| **Actions** | Score (promote to deep research), Defer (hold for later), Reject (remove from the funnel). |

Everything a person needs to decide is on this one panel: the fit score, the reasoning,
the firmographics, the evidence, and the three actions. The "View evidence" link opens the
original source signals (the actual job postings or news article) in a new tab.

---

## 13. The Needs-review queue

The second tab of the panel. These are the borderline companies, kept separate so they do
not clutter the confident Qualified list.

![Needs review](./images/disc_03_needs_review.png)

A company lands here for one of two reasons, shown by its review-origin tag:

- **AI unsure (ingest)**: at discovery time the AI was not confident enough to qualify or
  disqualify the company (confidence below 0.7, or the website could not be read). Most of
  the queue is this. These always wait for a human and are never auto-promoted.
- **Cooled from Watch (decayed)**: the company was qualified before, then its signals went
  cold and it dropped here. If it re-heats to Hot it is promoted straight back.

Each row carries its TTL hint, so a reviewer can see which companies are about to age out
and act first. A reviewer can Score, Defer, or Reject each one.

---

## 14. Cost controls

Discovery uses paid lookups (the company research uses live web search), so spend is
metered and capped:

- The panel shows a live budget bar, for example "Spend this month $61.54 / $250", split
  into Discovery versus Scored.
- The cap resets monthly and automatically stops paid work when it is reached, so the
  system can never run away with cost.
- The funnel itself is the biggest cost control: the free keyword gate and the cheap job
  qualifier mean the expensive company research only ever runs on companies that already
  look promising. Parked companies (single standard role) cost nothing at all.

---

## 15. Feature checklist

Everything the Discovery platform does, in one place.

**Finding companies**
- [x] Automatic weekday market scan for buying signals
- [x] Seven signal types: job postings, leadership change, executive engaged, layoff, acquisition, funding, event attendance
- [x] On-demand "Scan signals" plus the daily automatic run

**Filtering to real fits**
- [x] Three-layer funnel: free keyword gate, cheap job qualifier, web-research company qualifier
- [x] Job qualifier keeps only genuine operational RCM roles, rejecting educators, engineers, sales, clinical, and finance look-alikes
- [x] ICP qualifier with three segments (specialty, payer, health system) and explicit hard disqualifiers
- [x] Stacking and parking rule: a lone standard role is parked at no cost and auto-qualifies on a second role

**Scoring and ranking**
- [x] Transparent 0 to 100 intent score (auditable point system, no black box)
- [x] Base points by strongest signal, plus stacking, multi-signal, ABM, recency, and engagement bonuses
- [x] Hot versus Watch tiering with an auto-score threshold and a visible auto-score line

**Keeping the list honest (lifecycle)**
- [x] Watch TTL: qualified leads that go cold drop to Needs review after 7 days
- [x] Review TTL: Needs-review leads auto-reject after 7 days, and are restorable
- [x] Re-heat promotion: a cooled lead that goes Hot again is promoted back automatically
- [x] Per-row TTL hints so nothing rots silently
- [x] Review-origin tags ("AI unsure" versus "Cooled from Watch")

**The panel**
- [x] Status funnel: In queue, Qualified, Needs review, Disqualified
- [x] Filters: segment, signal type, ABM-list membership
- [x] Live cross-reference against the 2,687-account ABM list, with confirmed and name-only matches
- [x] Company detail panel: ICP confidence percent, "why qualified" reasoning, firmographics, view-evidence, and Score / Defer / Reject
- [x] Bulk select for routing many companies at once

**Cost**
- [x] Monthly spend meter with a hard cap
- [x] Funnel design and parking keep paid research minimal

---

*Next guides: Scored accounts (deep research dossier and warm intros), News, Watch list, and Engagement (Phase 2).*
