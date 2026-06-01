# Discovery Panel — UI Design Brief (v0)

> Hand this to Claude (design) to generate the UI. Everything here maps to data
> the backend already serves via `ReviewService`, so the design is buildable.

---

## 1. Product context

**Magical** sells an agentic-AI revenue-cycle-management (RCM) platform to US
healthcare organizations. An automated pipeline ("Auto Search") watches buying
signals — **layoffs, leadership changes (new CFO/CMO/etc.), acquisitions** —
finds companies showing them, and uses AI to qualify each against Magical's
Ideal Customer Profile (ICP).

**This UI is the Discovery Panel**: where **Galyna** (ABM analyst) reviews the
**qualified** companies the pipeline surfaced and decides what to do with each
— **Promote** (send to sales scoring), **Reject**, or **Defer**.

Only AI-**qualified** companies reach this panel. Disqualified ones are filtered
out upstream. Galyna's job here is the final human judgment + routing, not
hunting through noise.

**One user, desktop-first.** No mobile. Internal tool — clarity and speed over
marketing polish. Think **Linear / Notion / Vercel dashboard** aesthetic.

---

## 2. The data the UI renders (exact shape)

The UI calls one service. These are the real DTOs — design to them.

### `stats()` → DiscoveryStats (dashboard tiles)
```json
{ "qualified": 42, "needs_review": 8, "disqualified": 120,
  "error": 3, "total": 173, "panel_pending": 31 }
```

### `list_panel(segment?, signal_type?)` → PanelCompany[] (the queue)
Each company:
```json
{
  "company_key": "cullmanregionalmedicalcenter",
  "name": "Cullman Regional Medical Center",
  "segment": "health_system",          // specialty | payer | health_system | null
  "sub_segment": "community_hospital",  // free text or null
  "company_type": "provider",
  "approximate_employees": 1200,        // or null
  "confidence": 0.88,                   // 0.0–1.0  (AI's ICP confidence)
  "reasoning": "Regional community hospital in Alabama, ~1,200 staff; RCM is core ops.",
  "evidence_url": "https://cullmanregional.com/about",
  "domain": "cullmanregional.com",      // or null
  "review_status": "pending",           // always 'pending' in the panel
  "first_seen_at": "2026-06-01T09:39:00Z",
  "signal_count": 2,
  "signals": [
    { "source": "signalbase_leadership", "signal_type": "leadership_change",
      "summary": "New Chief Financial Officer", "observed_at": "2026-05-13T...",
      "strength": 0.90 },
    { "source": "signalbase_acquisitions", "signal_type": "acquisition",
      "summary": "Acquired by Big Health System ($250M)", "observed_at": "2026-05-20T...",
      "strength": 0.80 }
  ]
}
```

### Actions (return → effect)
- `promote(key)` → returns an account id; company leaves the panel.
- `reject(key, reason)` → company leaves the panel; reason stored.
- `defer(key)` → company leaves the panel (snoozed).

After any action the row disappears from the list and `panel_pending` drops by 1.

---

## 3. Screens

### Screen A — Discovery Panel (the main + only full screen)

```
┌───────────────────────────────────────────────────────────────────────────┐
│  Discovery Panel                                          [⟳ Run discovery] │
│                                                                             │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐                       │
│  │ 31       │ │ 42       │ │ 8        │ │ 173      │   ← stat tiles         │
│  │ In queue │ │Qualified │ │ Review   │ │ Total    │                       │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘                       │
│                                                                             │
│  Segment: [All ▾]   Signal: [All ▾]              31 companies awaiting you  │
│  ─────────────────────────────────────────────────────────────────────     │
│                                                                             │
│  ● Cullman Regional Medical Center      health system   ▓▓▓▓▓▓▓�f░ 88%       │
│    🟥 New CFO   🟧 Acquired ($250M)              2 signals · seen 2d ago    │
│    [ Promote ]  [ Defer ]  [ Reject ]                          [ details → ]│
│  ─────────────────────────────────────────────────────────────────────     │
│  ● Beacon Behavioral Health             specialty       ▓▓▓▓▓▓░░ 76%        │
│    🟦 New CMO                                    1 signal · seen 5h ago     │
│    [ Promote ]  [ Defer ]  [ Reject ]                          [ details → ]│
│  ─────────────────────────────────────────────────────────────────────     │
│  …                                                                          │
└───────────────────────────────────────────────────────────────────────────┘
```

**Stat tiles (top):** from `stats()`. Most prominent = **In queue**
(`panel_pending`). Others: Qualified, Needs review, Total. Tiles are
informational, not filters (v0).

**Filters:** two dropdowns — Segment (`specialty` / `payer` / `health_system`)
and Signal type (`layoff` / `leadership_change` / `acquisition`). They call
`list_panel(segment, signal_type)`.

**Company row** — the core component. Shows:
- Name (bold, primary)
- **Segment badge** (color-coded: specialty / payer / health_system)
- **Confidence meter** — `confidence` as a bar + `%`. Right-aligned. Color
  ramps green→yellow as confidence drops (≥0.85 green, 0.70–0.84 amber).
- **Signal chips** — one per `signals[]` entry, color-coded by signal_type,
  showing the `summary` (e.g. "New CFO", "Acquired ($250M)", "200 laid off").
- Meta line: `signal_count` · "seen {relative first_seen_at}"
- **Three actions:** Promote (primary), Defer (secondary), Reject (subtle/danger)
- "details →" opens the drawer (Screen B)

**Default sort:** most recently surfaced first (`first_seen_at` desc).

**Empty state:** friendly — "Nothing in the queue. Run discovery to surface new
companies." with a Run-discovery affordance (button can be a no-op/placeholder
in v0 — the cron does this; see §6).

### Screen B — Company Detail (right-side drawer, slides over the panel)

Opens on row click / "details →". Does **not** navigate away — overlay so Galyna
keeps her place.

```
┌────────────────────────────────────────────────┐
│  Cullman Regional Medical Center            ✕  │
│  cullmanregional.com ↗                          │
│  ┌──────────────┐                               │
│  │ health system│  community_hospital · provider│
│  └──────────────┘                               │
│                                                 │
│  ICP confidence            ▓▓▓▓▓▓▓░  88%        │
│                                                 │
│  ── Why qualified (AI) ────────────────────     │
│  "Regional community hospital in Alabama,       │
│   ~1,200 staff; RCM is a core operational       │
│   function."                                    │
│   Evidence: cullmanregional.com/about ↗         │
│                                                 │
│  ── Firmographics ─────────────────────────     │
│  Segment        Health System                   │
│  Sub-segment    Community hospital              │
│  Employees      ~1,200                          │
│  Domain         cullmanregional.com            │
│                                                 │
│  ── Why discovered (signals) ──────────────     │
│  🟥  New Chief Financial Officer                │
│      leadership · May 13 · strength 0.90        │
│  🟧  Acquired by Big Health System ($250M)      │
│      acquisition · May 20 · strength 0.80       │
│                                                 │
│  ────────────────────────────────────────       │
│  [ Promote to scoring ]   [ Defer ]  [ Reject ] │
└────────────────────────────────────────────────┘
```

Contents (all from the same `PanelCompany`):
- Header: name, `domain` as external link, segment badge + sub_segment +
  company_type
- Confidence meter (larger than the row's)
- **Why qualified** — the AI `reasoning` + `evidence_url` link (this is what
  earns Galyna's trust; make it prominent)
- **Firmographics** — segment, sub_segment, approximate_employees, domain
- **Why discovered** — the full `signals[]` list with summary, type, date,
  strength (the provenance / timeline)
- Action bar pinned at bottom: Promote / Defer / Reject

---

## 4. Actions & flows

| Action | UI | Result |
|---|---|---|
| **Promote** | Primary button | `promote(key)` → toast "Promoted to scoring", row removed, queue count −1 |
| **Reject** | Danger button → small modal asking **reason** (free text or quick chips: "too small", "wrong segment", "already a customer", "bad fit") | `reject(key, reason)` → toast, row removed |
| **Defer** | Secondary button | `defer(key)` → toast "Deferred", row removed |

- All three are **optimistic** (row animates out immediately; revert + error
  toast if the call fails).
- After an action in the drawer, the drawer closes and the row is gone.
- No bulk actions in v0 (one company at a time).

---

## 5. Components to design

1. **StatTile** — number + label; the "In queue" tile is emphasized.
2. **SegmentBadge** — 3 variants (specialty / payer / health_system) + a null
   fallback. Distinct, calm colors (not neon).
3. **ConfidenceMeter** — horizontal bar + `%`; color ramp by value. Two sizes
   (row + drawer).
4. **SignalChip** — icon + short summary, colored by signal_type:
   - 🟥 `layoff` · 🟦 `leadership_change` · 🟧 `acquisition`
   (use consistent icons; chips wrap if many).
5. **CompanyRow** — composes the above + action buttons.
6. **CompanyDrawer** — the detail overlay.
7. **RejectReasonModal** — reason chips + free text.
8. **Toast** — action confirmations.
9. **EmptyState** — for the zero-queue case.

---

## 6. States & edge cases

- **Loading**: skeleton rows in the list; skeleton blocks in the drawer.
- **Empty queue**: friendly empty state (see Screen A).
- **Long reasoning / many signals**: drawer scrolls; row truncates reasoning
  (reasoning is drawer-only; the row shows chips, not prose).
- **Null fields**: `segment`, `employees`, `domain`, `sub_segment` can be null —
  show "—" or hide the line; never render "null".
- **"Run discovery" button**: in v0 the cron populates data. The button can be a
  visible-but-disabled "Last run: {time}" affordance, or omitted. Don't design a
  full run-config UI — discovery is a backend cron.

---

## 7. Visual direction

- **Aesthetic:** Linear / Vercel dashboard — clean, dense-but-breathable,
  desktop. Subtle borders, generous white space, one accent color.
- **Palette:** neutral base (white / near-black text), calm segment colors,
  green→amber confidence ramp, restrained signal-chip colors. Avoid heavy reds
  except the Reject affordance.
- **Typography:** system / Inter. Company names prominent; meta text muted.
- **Density:** the list is the product — make scanning 30 companies fast.
  Confidence + signal chips should be readable at a glance without opening the
  drawer.
- **Motion:** quiet. Row removal animates out; drawer slides from the right.

---

## 8. Explicitly OUT of scope for v0 (don't design these yet)

- Account / campaign screens (the `accounts` table doesn't exist yet —
  Promote is a stub today).
- Bulk actions, saved views, assignment to owners.
- Editing a company or its verdict.
- Auth / settings / multi-user.
- The `needs_review` and `disqualified` queues (panel = qualified-pending only).
- Mobile layout.

Keep v0 to: **the queue, the filters, the stat tiles, the detail drawer, and
the three actions.** That is a complete, shippable review tool.
```
