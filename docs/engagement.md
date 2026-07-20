# Engagement: Complete Feature Guide

**Phase 2 of the ABM Intelligence Platform. The "who is raising their hand" layer.**

In one line: Engagement watches how our target accounts actually interact with us across
every channel (email, podcast, and Salesforce), rolls all of it into a single heat score per
account, shows which accounts are heating up right now, and hands a sales-ready packet to the
team for the hottest ones.

Phase 1 answers "which companies should we pursue, and how good a fit are they?" Engagement
answers the next question that actually drives revenue: "of all the accounts we touch, who is
showing buying behavior this week, and what do we do about it?" It turns scattered activity
(an email reply here, a podcast signup there, a meeting booked in Salesforce) into one ranked
worklist, with the warm contacts and the talking points attached.

*Audience: marketing and leadership. No technical background needed.*

---

## Contents

1. [Why it exists](#1-why-it-exists)
2. [The big idea: one heat score from every channel](#2-the-big-idea)
3. [The signals we capture and what each is worth](#3-the-signals-we-capture)
4. [The heat tiers](#4-the-heat-tiers)
5. [The three live sources](#5-the-three-live-sources)
6. [How a touch becomes heat (no inflation)](#6-how-a-touch-becomes-heat)
7. [Matching engagement to your accounts](#7-matching-engagement-to-your-accounts)
8. [Momentum: trend and the 8-week sparkline](#8-momentum)
9. [The console: header and controls](#9-the-console-header-and-controls)
10. [The "what needs you" action bar](#10-the-what-needs-you-action-bar)
11. [The Activity tab (the worklist)](#11-the-activity-tab)
12. [The Accounts tab (everything by heat)](#12-the-accounts-tab)
13. [Anatomy of an account row](#13-anatomy-of-an-account-row)
14. [The detail drawer](#14-the-detail-drawer)
15. [Activation: the Slack sales packet](#15-activation)
16. [Auto-activate Hot](#16-auto-activate-hot)
17. [Export and engagement rates](#17-export-and-engagement-rates)
18. [How it closes the loop with Discovery](#18-how-it-closes-the-loop)
19. [Cost controls](#19-cost-controls)
20. [Feature checklist](#20-feature-checklist)

---

## 1. Why it exists

A target list and a fit score tell us who to pursue. They do not tell us who is paying
attention to us right now. That information exists, but it is scattered: email engagement
lives in one tool, podcast leads in a spreadsheet, meetings and deals in Salesforce. No one
can hold all of it in their head, so the strongest buying signal of all (a known target
account suddenly engaging across several channels) goes unnoticed until a rep happens to
check the right tool on the right day.

Engagement fixes that by doing three things no person can do reliably by hand:

1. **Gather** every meaningful interaction from every channel into one place.
2. **Match** each one to the right account on our target and scored lists.
3. **Score and rank** the accounts by how hot their engagement is, so the team always knows
   who to call first, and surfaces the moment an account crosses into "Hot."

The output is a live worklist of accounts ordered by buying behavior, not guesswork, with the
evidence, the warm contacts, and the opening angle attached.

---

## 2. The big idea

The core idea is simple and powerful: **one heat score per account, built from every channel
at once.**

Every interaction is worth a number of points based on how strong a buying signal it is. A
booked meeting is worth far more than an email click. A sales-accepted opportunity is worth
far more than a content download. An account's heat is just the sum of its points across all
channels. Because everything rolls into one number on one scale, a marketer or an executive
can look at the board and instantly see which accounts are hot, regardless of which channels
those signals came from.

This is what makes the platform more than a dashboard. It is a single, honest measure of
buying behavior across the whole funnel.

---

## 3. The signals we capture

Each kind of interaction maps to a point value. This is the cross-channel scoring matrix, the
single source of truth for how heat is built. The strongest signals (the bottom-of-funnel,
"they are in a buying motion" ones) are worth the most.

| Signal | Points | What it is |
|--------|--------|------------|
| **High-intent lead** | 10 | An inbound contact or sales-form submission in Salesforce. Someone raised their hand directly. |
| **Sales-accepted opportunity** | 10 | A Salesforce opportunity marked as a qualified meeting. An active deal. |
| **Opportunity** | 10 | An open or won Salesforce opportunity. (Built and ready, not yet switched on in the live sync.) |
| **Meeting booked** | 10 | A meeting agreed or booked (from email outreach). |
| **Tradeshow** | 10 | A tradeshow lead that booked a qualified meeting. |
| **Reply** | 6 | A reply to an outreach email. Roughly a top-of-funnel lead. |
| **Podcast** | 4 | A podcast listener or download who is an ICP fit. |
| **TOFU content** | 2 | A top-of-funnel content download in Salesforce. Low intent. |
| **Click** | 1 | An email click. The lightest signal. |

A few interactions are tracked but deliberately score **zero**: an email being delivered,
opened, or bounced. We keep them because they power the open-rate and reply-rate percentages,
but they are not buying signals, so they add no heat. Keeping them explicit (rather than
silently ignored) means a reviewer can always see the full picture.

Adding a new channel later is a single new row in this matrix. Nothing else has to change,
which is why the platform can keep absorbing new signal sources over time.

---

## 4. The heat tiers

The total score maps to a plain-language tier, color-coded across the whole console:

| Tier | Score | Meaning |
|------|-------|---------|
| **Hot** | 21+ | Strong, multi-signal buying behavior. Act now. |
| **Warm** | 12 to 20 | Real, building engagement. Worth attention. |
| **Some** | 6 to 11 | Early signs of interest. |
| **Lower** | 0 to 5 | Light or incidental activity. |

"Hot" is the line that matters: it is the threshold for putting an account in front of a sales
rep, and the console is built to surface the moment an account crosses it.

---

## 5. The three live sources

Engagement pulls from three sources today. All three are **read-only** (we never write back
to any system) and **idempotent** (re-running a sync updates the picture, it never creates
duplicates). Each records when it last ran, so the console can show "Synced 5d" and the team
knows how fresh the board is.

| Source | What it brings in | How |
|--------|-------------------|-----|
| **Email (Reply.io)** | Clicks, replies, and booked meetings from outreach campaigns, plus delivered/opened/bounced counts for the rates. | Pulls all email activity since the start of 2026, so the board reflects the full cohort, not a rolling window. |
| **Podcast** | ICP-qualified podcast leads (the manually-vetted "Yes" and "Maybe" rows). | Reads a published Google Sheet as a simple file, with no login and no write access to the sheet. |
| **Salesforce** | High-intent inbound leads, tradeshow-qualified leads, top-of-funnel content leads, and sales-accepted opportunities. | Read-only Salesforce queries. Outbound rep call logs are deliberately excluded (they are rep activity, not buyer intent). |

Two more Salesforce signals (booked meetings and open/won opportunities) are already built and
tested, and can be switched on without new work when the team wants them.

---

## 6. How a touch becomes heat

Two rules keep the score honest, and they matter because they are exactly the kind of thing a
naive system gets wrong.

1. **One touch per contact, per kind.** If one person clicks ten emails, that is one "click"
   signal for that person, not ten. This stops a long contact list or a busy campaign from
   inflating an account's heat. Each contact contributes each kind of signal at most once.
2. **Account-level signals are deduplicated too.** Some signals (like sales-accepted
   opportunities) attach to the company, not a person. An account with twenty logged
   qualified meetings scores the meeting signal once (10 points), not 10 times 20. The score
   reflects that "this account has reached this stage," not how many times it was logged.

The result is a score you can trust to mean the same thing for every account, regardless of
company size or how active a campaign was.

---

## 7. Matching engagement to your accounts

Every incoming signal has to be tied to the right account before it can count. This is the
matching (or "crossing") step, and it follows a clear order of reliability:

1. **Email domain to a scored account's domain.** The strongest match (for example
   `@ochsner.org` to Ochsner Health System).
2. **Company name to a scored account's name.** When there is no usable domain.
3. **Domain or name to an ABM target.** For accounts on our target list that have not been
   independently scored yet.

A few deliberate rules make the matching trustworthy:

- **A company on both the scored list and the ABM list is one account**, tagged as belonging
  to both. We never show the same company twice.
- **An ABM-only match gets a stable placeholder identity that automatically heals** to the
  real scored account once that company gets scored. Engagement re-matches on every sync, so
  this happens on its own.
- **Personal email domains (Gmail, Yahoo, and similar) never match on domain.** A personal
  email falls through to a company-name match instead, so a stray personal address can never
  attach engagement to the wrong company.
- **Overly generic names (just "Medical" or "Health Group") do not match on name**, because
  they would catch unrelated companies. They can still match on domain, which is precise.
- **No match means the touch is not guessed at.** It is left unresolved rather than attached
  to the wrong account.

There is one important policy here: **we only track companies that are on the ABM target list
or the scored and discovery lists.** Engagement from a company we are not pursuing earns no
heat and no account on the board. (Since July 8, 2026, LinkedIn TOFU reactors are the one
exception on the contact side: every reactor is kept as a contact so the pipeline never pays
twice for the same person, but non-target companies still get no heat and no account.) This
keeps the board focused entirely on accounts that matter to us.

---

## 8. Momentum

Heat tells you how hot an account is. Momentum tells you which direction it is moving, and
that is often the more actionable signal.

For every account, the console keeps an **8-week history** of weekly points and draws it as a
small sparkline. From that history it derives two things:

- **Trend**: comparing the last two weeks against the prior two weeks gives "Heating up,"
  "Steady," or "Cooling."
- **This week**: the points the account has added in the most recent week, shown as
  "+N pts this wk."

An account that has been quietly Warm for months is a different conversation from one that
just added 27 points this week. Momentum is what lets the team spot the second kind instantly.

---

## 9. The console: header and controls

The screen is titled "Engagement" with the line "Buyer intent across email, podcast and
Salesforce, matched to your accounts, ranked by heat," followed by how long ago it last
synced.

![The Engagement console, Activity tab](./images/eng_01_activity.png)

Three controls sit in the top right:

| Control | What it does |
|---------|--------------|
| **Sync** | Pulls the latest engagement from the sources and re-scores the board. |
| **Export CSV** | Downloads the whole board as a spreadsheet for sales to work in (see section 17). |
| **Auto-activate Hot** | A toggle that, when on, automatically sends every Hot account to Slack once (see section 16). |

---

## 10. The "what needs you" action bar

Directly under the header is a single bar that answers "what should I do right now?" before
the team even looks at the list. It surfaces the few things that need attention, for example:

- **"1 account just went Hot"**: an account crossed the Hot threshold this week. The most
  time-sensitive thing on the screen, so it leads.
- **"23 Hot accounts"**: how many accounts are currently Hot.

If nothing needs action, the bar simply says "You're all caught up." On the right it shows the
totals, for example "754 accounts, 200 touches," so the scale of what is being tracked is
always visible. Each item is clickable and jumps straight to the relevant list.

---

## 11. The Activity tab

Activity is the **worklist**: the short list of accounts that actually moved recently, so a
rep can work top to bottom without sifting through the whole database.

It shows only accounts with a recent meaningful touch, and it deliberately ignores noise:
a lone click or a top-of-funnel download does not make an account "move." Accounts that just
jumped a tier are pushed to the very top, then the rest are ordered by how recently they
moved. The column on the right, "What changed," names the most significant recent signal (for
example "Sales accepted" or "High-intent lead") and when it happened.

This is the default view, because it answers the daily question: who got hotter, and why?

---

## 12. The Accounts tab

Accounts is the **full list**, every engaged account ordered by total heat (highest first).

![The Engagement console, Accounts tab](./images/eng_02_accounts.png)

Where Activity is "what changed lately," Accounts is "the complete standing ranking." The
right-hand column here shows the last touch rather than what changed, and the same segment
filter applies. This is the view for working down the entire book of engaged accounts, or for
checking where a specific account stands.

Both tabs can be filtered by segment (Health System, Specialty, Payer), so a campaign owner
can focus on just their part of the market.

---

## 13. Anatomy of an account row

Each row carries the whole engagement story of one account at a glance.

| Element | What it tells you |
|---------|-------------------|
| **Account name** | The company. Clicking the row opens the detail drawer. |
| **Segment badge** | Health System, Specialty, or Payer. |
| **ABM badge** | Whether the account is on our ABM target list. |
| **Sub-line** | The number of contacts engaging, the domain, and the fit classification. |
| **Heat** | A colored dot, the score, and the tier word (for example "33 Hot"). |
| **Momentum** | The 8-week sparkline plus the trend and "+N pts this week." |
| **What changed / Last touch** | The most significant recent signal and when (Activity), or the last touch time (Accounts). |
| **Activate** | On Hot rows only: a one-click button to send the account to Slack. |

---

## 14. The detail drawer

Clicking any account opens a drawer with the full engagement picture for that account.

![The account drawer: momentum and breakdown](./images/eng_03_drawer_top.png)

It has four parts, top to bottom:

- **Engagement momentum** (the hero): the total score and tier, the trend with this week's
  gain, and the full 8-week sparkline from "8 weeks ago" to "this week."
- **Score breakdown**: exactly how the score is built, one row per signal kind with its
  point contribution and a bar, totaling to the heat score. This is the transparency that
  lets anyone see why an account is as hot as it is (for example Click +13, Sales accepted
  opp +10, Tradeshow +10).
- **Contacts engaging**: the people at the account who are interacting, shown as avatars with
  a count.
- **Engagement timeline**: every touch grouped by kind and day, newest at the bottom, each
  with its points (for example "Click x2, Jun 13, +2 pts"). The full history behind the
  score.

![The drawer: score breakdown and timeline](./images/eng_04_drawer_timeline.png)

A Hot account's drawer has an "Activate to SDR" button in the footer, which starts the
activation flow.

---

## 15. Activation

Activation is how a hot account gets handed to the sales team. It is the payoff of the whole
system: not just "this account is hot," but a complete, ready-to-act packet delivered where
the team works.

Clicking Activate opens a preview of the Slack message that will be posted, so nothing is sent
blindly.

![The activation preview](./images/eng_05_activate_modal.png)

When confirmed, two things happen behind the scenes, and then the packet is posted to the
engagement Slack channel:

1. **Decision-maker enrichment (paid, on demand).** The system finds the relevant
   decision-makers at the account and resolves each one's verified work email and mobile
   phone. This costs credits, so it runs **only** when someone activates an account, never
   automatically in the background. If it cannot complete, it gracefully falls back to the
   names and titles it does have.
2. **The SDR intel brief (free, instant).** The system assembles a "why now" brief by reusing
   research we already hold from Phase 1: the discovery triggers that put the account in the
   funnel, the entry timing, recent news, and a recommended opening angle. Because it reuses
   stored data, it costs nothing and adds no delay.

The resulting Slack card is a full sales packet: the account name and heat tier, the signal
breakdown, the fit classification and which lists it is on, the intel brief (why now,
triggers, recent news, opening angle), the enriched decision-makers with their contact
details, and a button to open the account in the console. The assigned rep is shown as plain
text for now (not an @-mention), so the format can be tuned before anyone is pinged.

---

## 16. Auto-activate Hot

For teams that want the worklist to run itself, an "Auto-activate Hot" toggle in the header
will automatically activate every Hot account once: enrich it and post its packet to Slack,
exactly as a manual activation would. It is careful about this:

- Each account is activated **once and only once**. The system remembers what it has already
  sent, so a Hot account never spams the channel on every refresh.
- It works through accounts one at a time rather than all at once, so it never floods Slack or
  the enrichment service.

This mirrors the auto-score behavior in Discovery: a human can stay in the loop by leaving it
off, or let the hottest accounts flow to sales automatically by turning it on.

---

## 17. Export and engagement rates

**Export CSV** downloads the entire board as a spreadsheet, one row per account, with the full
intent payload: the account and domain, its classification and fit tier, which lists it is on,
its heat tier and score, the trend and this week's change, the contact count, the counts of
clicks, replies, and meetings, the open and reply rates, and the last touch date. This is the
artifact a sales team can sort and work in their own tools.

The console also derives **open rate and reply rate** per account from the email data
(opened over delivered, replied over delivered), so the quality of engagement, not just the
volume, is visible.

---

## 18. How it closes the loop

Engagement is not a dead end. It feeds back into Phase 1: an account that is engaging with us
**ranks higher in Discovery's intent scoring**. A Warm or Hot engagement tier adds to the
account's buying-intent score, so the two systems reinforce each other. A company we found in
Discovery, scored for fit, and then saw start engaging will rise to the top of both boards.

This is what makes the platform a closed loop rather than four separate tools: discovery feeds
scoring, scoring feeds outreach, and engagement feeds back into discovery's ranking.

---

## 19. Cost controls

Engagement is inexpensive to run, and the one paid step is tightly controlled.

- **Gathering signals is essentially free.** The email, podcast, and Salesforce pulls are
  read-only API and file reads, with no per-item AI cost.
- **The only paid step is decision-maker enrichment**, and it runs **only on activation**, on
  the specific accounts a rep (or the auto-activate toggle) chooses to action. Nothing is
  enriched speculatively.
- **The intel brief is free**, because it reuses research the platform already paid for in
  Phase 1.
- **Test posts spend nothing.** A wiring-test activation skips the paid enrichment entirely.

So the spend tracks exactly the value: money is spent only to package an account the team has
decided to pursue.

---

## 20. Feature checklist

Everything the Engagement platform does, in one place.

**One score from every channel**
- [x] A single heat score per account, summed from all channels
- [x] Cross-channel scoring matrix: high-intent lead, sales-accepted opp, opportunity, meeting, tradeshow (10); reply (6); podcast (4); TOFU content (2); click (1)
- [x] Delivered, opened, and bounced tracked at zero points to power the rates
- [x] Heat tiers: Hot 21+, Warm 12 to 20, Some 6 to 11, Lower 0 to 5
- [x] A new channel is one new row in the matrix

**The sources**
- [x] Email engagement (Reply.io): clicks, replies, meetings, plus open/reply rates
- [x] Podcast leads (ICP-qualified) from a published sheet, read-only
- [x] Salesforce: high-intent inbound leads, tradeshow-qualified, TOFU content, sales-accepted opportunities
- [x] All sources read-only and idempotent, with a "last synced" stamp
- [x] Outbound rep call logs excluded (activity, not intent)
- [x] Booked meetings and open/won opportunities built and ready to switch on

**Honest scoring**
- [x] One touch per contact per kind, so a long contact list cannot inflate heat
- [x] Account-level signals deduplicated, so repeated logging does not multiply the score

**Matching to accounts**
- [x] Match by email domain, then company name, then ABM target
- [x] A company on both lists is one account, tagged to both
- [x] ABM-only matches self-heal to the scored account once scored
- [x] Personal email domains and overly generic names guarded against false matches
- [x] Only ABM and scored/discovery companies are tracked; other engagement is dropped (one exception since 2026-07-08: LinkedIn TOFU reactors are captured as contacts regardless of company, for dedup — but they earn no heat and no account)
- [x] No match is left unresolved, never guessed

**Momentum and the console**
- [x] 8-week momentum sparkline per account
- [x] Trend (heating up, steady, cooling) and this-week point gain
- [x] Activity tab: the worklist of accounts that moved recently, tier-jumps first, noise filtered
- [x] Accounts tab: the full list by total heat
- [x] Segment filter (Health System, Specialty, Payer)
- [x] A "what needs you" action bar with just-went-Hot and Hot counts

**The drawer**
- [x] Momentum hero with score, tier, trend, and the 8-week chart
- [x] Score breakdown by signal kind, totaling to the heat score
- [x] Contacts engaging
- [x] Full engagement timeline grouped by kind and day

**Activation**
- [x] One-click activation of a Hot account to Slack, with a preview first
- [x] A full Slack sales packet: heat, signal breakdown, classification, lists, intel brief, and decision-makers
- [x] SDR intel brief (why now, triggers, recent news, opening angle) reusing stored research, free
- [x] Decision-maker enrichment with verified email and mobile, paid, only on activation
- [x] Auto-activate Hot toggle, once-each, paced, deduplicated
- [x] Graceful fallback if enrichment cannot complete; test posts spend nothing

**Beyond the board**
- [x] Export the whole board to CSV with the full intent payload
- [x] Open-rate and reply-rate per account
- [x] Engaged accounts rank higher in Discovery, closing the loop

---

*This completes the platform documentation: Discovery, Scored, News, Watch list, and
Engagement.*
