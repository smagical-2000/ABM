# News: Complete Feature Guide

**Part of Phase 1 of the ABM Intelligence Platform. The market-timing layer.**

In one line: News is an always-on reading of the healthcare revenue-cycle market, every
relevant headline pulled in, explained in plain terms, scored for how worth acting on it is
right now, and turned into a ready-to-use outreach angle.

The other panels are about specific companies. News is different: it is about the *industry*.
It watches the rules, the regulations, and the market moves that change what our buyers care
about (a new CMS prior-authorization mandate, a competitor launching an AI denials product,
a record-high denial-rate report) and tells the team how to ride that wave into a
conversation. It is the difference between cold outreach and outreach that lands the week a
buyer is already thinking about the problem.

*Audience: marketing and leadership. No technical background needed.*

---

## Contents

1. [Why it exists](#1-why-it-exists)
2. [What it is, and what it is not](#2-what-it-is-and-what-it-is-not)
3. [Where the headlines come from](#3-where-the-headlines-come-from)
4. [The six topics we track](#4-the-six-topics-we-track)
5. [The AI triage pass](#5-the-ai-triage-pass)
6. [The "get behind" score](#6-the-get-behind-score)
7. [The News feed screen](#7-the-news-feed-screen)
8. [Anatomy of a news card](#8-anatomy-of-a-news-card)
9. [How a refresh works](#9-how-a-refresh-works)
10. [Cost controls](#10-cost-controls)
11. [Feature checklist](#11-feature-checklist)

---

## 1. Why it exists

The best time to reach a buyer is the moment the market hands them a reason to care. A new
federal rule with a compliance deadline, a competitor's product launch, a report showing
denials at a record high: each of these briefly raises the priority of a problem we solve,
across a whole category of buyers at once.

The problem is that staying on top of that takes constant reading. Someone would have to
follow trade publications, CMS announcements, and healthcare-AI news every day, judge which
items actually matter to our sale, and translate each one into a talking point. That does
not scale, and it usually does not happen.

News closes that gap. It reads the market continuously, keeps only what is relevant to
revenue-cycle buyers, explains why each item matters in one line, scores how strongly we
should act on it, and writes the opening move. The team gets timing and talking points
without doing the reading.

---

## 2. What it is, and what it is not

This is the distinction that makes News useful, so it is worth being explicit.

| | News (this panel) | Discovery and Scored |
|--|-------------------|----------------------|
| **What it watches** | The industry: rules, regulations, market and competitor moves | Specific companies |
| **The unit** | A headline | A company |
| **The question** | What is happening in the market that we can use? | Which companies should we pursue, and how good a fit are they? |
| **What you do with it** | Time outreach and pick talking points | Decide who to target and how |

News does not surface buying signals on individual accounts. It is the market backdrop that
makes the per-company outreach land better. The two work together: Discovery and Scored tell
you *who* to call; News tells you *what to say this week and why now*.

---

## 3. Where the headlines come from

The feed is built from Google News, which publishes a free, reliable headline feed for any
search. We run one tight, carefully written search per topic, so the coverage is broad but
stays on revenue-cycle subjects and does not drift into generic health or clinical news. No
paid news service and no API key are needed, so the sourcing itself costs nothing.

A few deliberate choices keep the feed clean:

- **Recency:** each search is bounded to roughly the last 60 days, so old articles do not
  resurface as if they were new. The screen then lets you narrow further to 7, 30, or 90
  days.
- **Deduplication:** the same story often appears under more than one topic search. Each
  unique article is kept once, by its link.
- **Clean titles:** publications' names are stripped out of the headline text and shown
  separately as the source, so every card reads consistently.

---

## 4. The six topics we track

Every headline is sorted into exactly one topic. These are the topics the feed filters on,
and they map directly to the problems we sell into:

| Topic | What it covers |
|-------|----------------|
| **Prior Auth** | Prior-authorization rules, payer behavior, and automation of the approval process. |
| **Denials** | Claim denials and denials management, a core revenue-cycle pain. |
| **RCM / AI** | Revenue-cycle automation and artificial intelligence, including the competitive landscape. |
| **Eligibility** | Insurance, benefits, and eligibility verification. |
| **CMS / Policy** | Federal rules, regulations, final rules, and mandates that affect billing, claims, and reimbursement. |
| **Operations** | General revenue-cycle operations that do not fall cleanly into the others. |

The dedicated searches cover prior auth, denials, revenue-cycle AI, eligibility, and CMS
policy. The "Operations" topic is used by the AI triage pass when an article is clearly
about revenue-cycle operations but does not fit one of the sharper buckets.

---

## 5. The AI triage pass

Raw headlines are noisy, so once new articles are pulled, a single inexpensive AI pass reads
the day's new titles and does five things to each one. It runs over all the new headlines at
once (it reads only the titles, never browsing the articles), so it costs a few cents a day.

| Step | What the AI decides |
|------|---------------------|
| **Relevant or not** | Is this genuinely about healthcare revenue cycle and reimbursement (billing, claims, prior auth, denials, eligibility, payer or CMS policy, or AI that automates that work)? Anything generic, clinical, pharma, device, or unrelated is dropped, so the feed stays on-subject. |
| **Topic** | The single best-fit topic from the six above. |
| **Why it matters** | One sentence of genuine analysis: the "so what" the headline does not already say. Not a restatement of the title, but the market shift it signals, the buyer pain or budget pressure it exposes, or what it changes for a revenue-cycle buyer's priorities. |
| **Get behind** | A 0 to 100 score for how hard we should get behind this as an outreach angle right now (see section 6). |
| **The play** | One sentence a rep can use: who to target and the angle that ties this story to our value, written to read differently from "why it matters." |

The design rule is that the three pieces of text must each add something different: the
title is the fact, "why it matters" is the implication, and "the play" is the move. They are
never three versions of the same sentence.

It also fails safely. If the AI pass ever has trouble, the affected headlines simply keep
their feed-assigned topic and stay in the feed, so a glitch can never blank the page.

---

## 6. The "get behind" score

This is the number that turns a feed into a priority list. Each relevant headline gets a
score from 0 to 100 for how strongly we should get behind it as an outreach wedge at this
moment. It is high when all three of these are true:

1. **We directly solve the problem it raises** (prior auth, denials, eligibility, claims),
   not a tangential one.
2. **It is urgent**, for example a new rule, mandate, deadline, or a record-high statistic.
3. **It is broad**, for example national or all-payer, rather than one regional provider.

It is low when the story is narrow, regional, vague, or only loosely about reimbursement.

On the screen this becomes a simple two-state label:

- **Get behind** (highlighted): a score of 70 or more. This is a story worth building
  outreach around now.
- **Context** (muted): a lower score. Useful background, not an immediate wedge.

The feed is ranked by this score, with the newest stories breaking ties, so the most
actionable items sit at the top of the page.

---

## 7. The News feed screen

The screen is titled "RCM and regulation news" with the line "Market intelligence: CMS
rules, prior auth, denials, eligibility, healthcare-AI. Timing and talking points for
outreach."

![The News feed](./images/news_01_feed.png)

The controls are deliberately simple:

| Control | What it does |
|---------|--------------|
| **Refresh** | Pulls the latest headlines and tags the new ones. Runs in the background, one refresh at a time, and tells you how many new items it added when it finishes. |
| **Topic chips** | "All" plus the six topics. Click one to filter the feed to that subject. |
| **Window** | Last 7 days, Last 30 days (the default), or Last 90 days. |

Selecting a single topic narrows the feed to just those stories, which is how a campaign
owner focused on, say, prior authorization can read only what is relevant to them.

![The feed filtered to Prior Auth](./images/news_02_topic_prior_auth.png)

---

## 8. Anatomy of a news card

Each card is one headline, presented so a reader gets the fact, the implication, and the
action at a glance.

| Element | What it tells you |
|---------|-------------------|
| **Topic chip** | Which of the six subjects this story belongs to, color-coded. |
| **Source and time** | The publication and how long ago it was published. |
| **Get-behind badge** | "Get behind" with the score for strong wedges (70+), or "Context" with the score for background. Shown only when there is a score. |
| **Headline** | The article title, linking out to the full story in a new tab. |
| **Why it matters** | The one-line implication: the "so what" behind the headline. |
| **The play** | The recommended outreach move: who to target and the angle to open with, in a highlighted box. |

Stronger, more actionable stories carry the full set (why it matters and a play); lighter
context items may show just the headline and topic. The result is that a rep can scan the
page and, for any high "get behind" item, already have the opening line for an email.

---

## 9. How a refresh works

The feed stays current in two ways: a daily automatic job, and the manual Refresh button for
when someone wants the latest immediately. Both do the same thing.

1. **Pull** the latest headlines across all topic searches.
2. **Keep only the new ones.** Articles already in the feed are skipped, so we never re-pay
   to analyze a story we have already seen.
3. **Triage** the new headlines through the AI pass (topic, why it matters, get-behind, the
   play), and **drop the irrelevant ones**.
4. **Store** what is left, so the feed builds into a running, deduplicated archive rather
   than resetting each time.

Each run records a short summary (how many were fetched, how many were new, how many were
stored, and how many were dropped as irrelevant), and the screen shows when the feed was
last updated.

---

## 10. Cost controls

News is the cheapest panel to run, by design.

- **Sourcing is free:** the headlines come from a free feed, with no paid news service and
  no API key.
- **Analysis is pennies a day:** the AI triage reads only headline text, in one batched pass
  over just the new articles, and never browses the full articles or runs web research.
- **No repeat spend:** only genuinely new headlines are ever analyzed, so the cost does not
  grow as the archive does.
- **Metered anyway:** the small analysis cost is still recorded against the platform's shared
  monthly budget, so every dollar across the platform is accounted for in one place.

---

## 11. Feature checklist

Everything the News platform does, in one place.

**Sourcing the market**
- [x] Free, reliable headline sourcing with no paid service and no API key
- [x] One tight search per topic, kept on revenue-cycle subjects
- [x] Bounded to recent news (about 60 days) so old stories do not resurface
- [x] Deduplicated by link, with publication names cleaned out of titles

**Making it useful (AI triage)**
- [x] Relevance filter that drops generic, clinical, pharma, device, and unrelated news
- [x] Automatic sorting into six topics (prior auth, denials, RCM/AI, eligibility, CMS/policy, operations)
- [x] A one-line "why it matters" that adds analysis, not a restatement of the headline
- [x] A one-line "play": who to target and the angle to open with
- [x] Fails safely, a glitch never blanks the feed

**Prioritizing**
- [x] A 0 to 100 "get behind" score for how strong an outreach wedge each story is now
- [x] "Get behind" versus "Context" labeling at a 70 threshold
- [x] Feed ranked by get-behind, newest breaking ties

**The screen**
- [x] Topic filter chips plus an "All" view
- [x] Time window of 7, 30, or 90 days
- [x] One-click Refresh, running in the background, with a new-item count
- [x] Cards showing topic, source, time, get-behind, headline link, why it matters, and the play

**Keeping it current and cheap**
- [x] Daily automatic refresh plus on-demand manual refresh
- [x] Only new headlines are analyzed and stored (a running, deduplicated archive)
- [x] Pennies-a-day analysis cost, metered against the shared monthly budget

---

*Next guides: Watch list, and Engagement (Phase 2).*
