const { useState, useEffect, useMemo, useRef } = React;

// Per-source company cap applied when the run form is left blank. A manual run
// is never silently unlimited (that's the runaway-spend footgun) — keep this in
// sync with the server's DISCOVERY_MANUAL_DEFAULT_LIMIT.
const DEFAULT_RUN_LIMIT = 10;

// ── Skeleton loading row ────────────────────────────────────────────────────
function SkeletonRow() {
  return (
    <div className="border-b border-zinc-100 px-6 py-4">
      <div className="flex items-center gap-4">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2.5">
            <div className="h-4 w-56 rounded bg-zinc-100" />
            <div className="h-4 w-24 rounded-full bg-zinc-100" />
          </div>
          <div className="mt-3 flex gap-1.5">
            <div className="h-6 w-32 rounded-md bg-zinc-100" />
            <div className="h-6 w-28 rounded-md bg-zinc-100" />
          </div>
          <div className="mt-3 h-3 w-40 rounded bg-zinc-100" />
        </div>
        <div className="hidden h-6 w-28 rounded bg-zinc-100 md:block" />
        <div className="h-8 w-24 rounded-lg bg-zinc-100" />
      </div>
    </div>
  );
}

function TabButton({ active, onClick, label, count, accent }) {
  return (
    <button onClick={onClick}
      className={`relative -mb-px flex items-center gap-2 px-3.5 py-2.5 text-[13px] font-medium transition-colors ${active ? 'text-zinc-900' : 'text-zinc-400 hover:text-zinc-600'}`}>
      {label}
      <span className={`rounded-full px-1.5 py-0.5 text-[11px] tabular-nums ${active ? (accent === 'amber' ? 'bg-amber-100 text-amber-700' : 'bg-indigo-100 text-indigo-700') : 'bg-zinc-100 text-zinc-400'}`}>{count}</span>
      {active && <span className="absolute inset-x-0 -bottom-px h-0.5 rounded-full bg-zinc-900"></span>}
    </button>
  );
}

// ── Header nav switch (Discovery | Scored) ──────────────────────────────────
function NavSwitch({ view, onChange, scoredCount, pulse }) {
  const item = (key, label) => {
    const active = view === key;
    return (
      <button onClick={() => onChange(key)}
        className={`relative flex items-center gap-1.5 rounded-md px-3 py-1.5 text-[13px] font-medium transition-all ${active ? 'bg-white text-zinc-900 shadow-sm ring-1 ring-zinc-200/70' : 'text-zinc-500 hover:text-zinc-700'}`}>
        {label}
        {key === 'scored' && (
          <span className={`rounded-full px-1.5 py-0.5 text-[10.5px] tabular-nums transition-all ${active ? 'bg-indigo-100 text-indigo-700' : 'bg-zinc-200/70 text-zinc-500'} ${pulse ? 'ring-2 ring-indigo-300' : ''}`}>{scoredCount}</span>
        )}
      </button>
    );
  };
  return (
    <div className="flex items-center gap-0.5 rounded-lg bg-zinc-100/80 p-0.5">
      {item('discovery', 'Discovery')}
      {item('scored', 'Scored')}
      {item('news', 'News')}
    </div>
  );
}

// ── per-account decision ticker (fading corner feed) ────────────────────────
const DECISION_META = {
  qualified:    { label: 'Qualified',    icon: '✓', cls: 'text-emerald-600', bg: 'bg-emerald-50', ring: 'ring-emerald-200' },
  disqualified: { label: 'Disqualified', icon: '✕', cls: 'text-zinc-400',    bg: 'bg-zinc-50',    ring: 'ring-zinc-200' },
  needs_review: { label: 'Needs review', icon: '~', cls: 'text-amber-600',   bg: 'bg-amber-50',   ring: 'ring-amber-200' },
  error:        { label: 'Errored',      icon: '!', cls: 'text-rose-500',    bg: 'bg-rose-50',    ring: 'ring-rose-200' },
};

function ActivityFeedItem({ item }) {
  const [shown, setShown] = useState(false);
  useEffect(() => { const t = setTimeout(() => setShown(true), 20); return () => clearTimeout(t); }, []);
  const m = DECISION_META[item.status] || DECISION_META.disqualified;
  const visible = shown && !item.leaving;
  return (
    <div className={`flex items-center gap-2.5 rounded-xl border border-zinc-200/70 bg-white/95 px-3 py-2 shadow-lg shadow-zinc-900/5 backdrop-blur transition-all duration-500 ${visible ? 'translate-x-0 opacity-100' : '-translate-x-3 opacity-0'}`}>
      <span className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-[12px] font-bold ${m.bg} ${m.cls} ring-1 ring-inset ${m.ring}`}>{m.icon}</span>
      <div className="min-w-0">
        <div className="max-w-[200px] truncate text-[12.5px] font-medium text-zinc-800">{item.name}</div>
        <div className={`text-[11px] ${m.cls}`}>{m.label}{item.segment ? ` · ${item.segment.replace('_', ' ')}` : ''}</div>
      </div>
    </div>
  );
}

function ActivityFeed({ items }) {
  if (!items.length) return null;
  return (
    <div className="pointer-events-none fixed bottom-5 left-5 z-40 flex flex-col gap-2">
      {items.map((it) => <ActivityFeedItem key={it.id} item={it} />)}
    </div>
  );
}

// ── ActivityBanner — live "processing" marker shown while a run is in flight ─
// Shows true progress when the run has reported how many companies it plans to
// qualify (`planned`, the denominator): "42% · 21 of 50 evaluated". Until that
// lands (sources still being pulled), it falls back to the indeterminate copy.
function fmtElapsed(sec) {
  if (sec == null || sec < 0) return null;
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return m > 0 ? `${m}m ${s}s` : `${s}s`;
}
function ActivityBanner({ runs, paused, cancelling, phase }) {
  const qualified = runs.reduce((n, r) => n + (r.companies_qualified || 0), 0);
  const evaluated = runs.reduce((n, r) => n + (r.new_companies || 0), 0);
  const planned = runs.reduce((n, r) => n + (r.planned || 0), 0);
  const elapsed = fmtElapsed(Math.max(0, ...runs.map((r) => r.elapsed_seconds || 0)));
  const sources = [...new Set(runs.map((r) => r.source))].join(', ');
  const hasPlan = planned > 0;
  // Cap at 99% until the run actually finishes, so it never reads "100%" while
  // the last company is still being qualified.
  const pct = hasPlan ? Math.min(99, Math.round((evaluated / planned) * 100)) : null;
  // Paused/cancelling re-skin the banner so the state is unmistakable.
  const tone = cancelling
    ? { wrap: 'border-rose-100 from-rose-50 to-rose-50/40', dot: 'bg-rose-500', text: 'text-rose-700', sub: 'text-rose-600' }
    : paused
    ? { wrap: 'border-amber-100 from-amber-50 to-amber-50/40', dot: 'bg-amber-500', text: 'text-amber-700', sub: 'text-amber-600' }
    : { wrap: 'border-indigo-100 from-indigo-50 to-violet-50/50', dot: 'bg-indigo-500', text: 'text-indigo-700', sub: 'text-indigo-600' };
  // `phase` (e.g. "Scanning LinkedIn engagement") labels which run is live; it's
  // the only signal when there are no connector_runs (the social scan has none).
  const verb = cancelling ? 'Stopping' : paused ? 'Paused' : (phase || 'Discovering');
  const label = sources ? `${verb} — ${sources}` : verb;
  return (
    <div className={`border-b bg-gradient-to-r ${tone.wrap}`}>
      <div className="mx-auto flex max-w-6xl items-center gap-3 px-8 py-2.5">
        <span className="relative flex h-2.5 w-2.5">
          {!paused && !cancelling && <span className={`absolute inline-flex h-full w-full animate-ping rounded-full opacity-75 ${tone.dot}`}></span>}
          <span className={`relative inline-flex h-2.5 w-2.5 rounded-full ${tone.dot}`}></span>
        </span>
        <span className={`text-[13px] font-medium ${tone.text}`}>{label}</span>
        <span className="text-zinc-300">·</span>
        {hasPlan ? (
          <>
            <span className={`text-[13px] font-semibold tabular-nums ${tone.text}`}>{pct}%</span>
            <div className="hidden h-1.5 w-40 overflow-hidden rounded-full bg-black/5 sm:block">
              <div className={`h-full rounded-full transition-all duration-500 ${tone.dot}`} style={{ width: `${pct}%` }} />
            </div>
            <span className={`text-[13px] tabular-nums ${tone.sub}`}>
              {evaluated} of {planned} evaluated · {qualified} qualified
            </span>
          </>
        ) : (
          <span className={`text-[13px] tabular-nums ${tone.sub}`}>
            {evaluated > 0 ? `${qualified} qualified of ${evaluated} evaluated` : 'scanning sources…'}
          </span>
        )}
        {elapsed && <><span className="text-zinc-300">·</span><span className={`text-[12px] tabular-nums ${tone.sub}`}>{elapsed}</span></>}
        <span className="ml-auto hidden items-center gap-1.5 text-[12px] text-zinc-400 sm:flex">
          {paused ? 'spend frozen' : <><Icons.refresh className="h-3.5 w-3.5 animate-spin" />updating live</>}
        </span>
      </div>
    </div>
  );
}

// ── DiscoverySpendMeter — month-to-date qualify spend vs the discovery budget ─
function DiscoverySpendMeter({ spend, lastRun }) {
  if (!spend) return null;
  const spent = spend.month_discovery_cost || 0;
  const budget = spend.discovery_budget || 0;
  const est = spend.discovery_est_qual_cost || 0.12;
  const pct = budget ? Math.min(100, Math.round((spent / budget) * 100)) : 0;
  const over = budget && spent >= budget;
  const near = !over && budget && spent >= budget * 0.8;
  const bar = over ? 'bg-rose-500' : near ? 'bg-amber-500' : 'bg-indigo-500';
  const tone = over ? 'text-rose-600' : near ? 'text-amber-600' : 'text-zinc-500';
  const runCost = lastRun && lastRun.cost_usd;
  const runEval = lastRun && (lastRun.evaluated ?? (
    (lastRun.qualified || 0) + (lastRun.needs_review || 0) + (lastRun.disqualified || 0)
  ));
  return (
    <div className="mt-3 flex flex-wrap items-center gap-3 rounded-xl border border-zinc-200 bg-white px-4 py-2.5 shadow-sm shadow-zinc-900/[0.02]">
      <Icons.zap className="h-4 w-4 shrink-0 text-zinc-400" />
      <span className="text-[12.5px] font-medium text-zinc-600">Discovery spend</span>
      <span className={`text-[12.5px] font-semibold tabular-nums ${tone}`}>
        ${spent.toFixed(2)}{budget ? ` / $${budget.toFixed(0)}` : ''}
      </span>
      {budget > 0 && (
        <div className="hidden h-1.5 w-32 overflow-hidden rounded-full bg-zinc-100 sm:block">
          <div className={`h-full rounded-full transition-all duration-500 ${bar}`} style={{ width: `${pct}%` }} />
        </div>
      )}
      {runEval > 0 && (
        <span className="text-[11.5px] tabular-nums text-indigo-600">
          Last run: {runEval} evaluated{runCost != null ? ` · $${Number(runCost).toFixed(2)}` : ''}
        </span>
      )}
      <span className="ml-auto text-[11.5px] tabular-nums text-zinc-400">
        ~${est.toFixed(2)}/company · month to date
      </span>
      {over ? (
        <span className="rounded-full bg-rose-50 px-2 py-0.5 text-[11px] font-medium text-rose-600 ring-1 ring-inset ring-rose-100">Budget reached</span>
      ) : near ? (
        <span className="rounded-full bg-amber-50 px-2 py-0.5 text-[11px] font-medium text-amber-600 ring-1 ring-inset ring-amber-100">Near budget</span>
      ) : null}
    </div>
  );
}

// ── LastRunSummary — what the most recent manual run produced, per source ─────
function LastRunSummary({ lastRun }) {
  if (!lastRun || !lastRun.at) return null;
  const sources = lastRun.by_source || {};
  const entries = Object.entries(sources);
  if (!entries.length && !lastRun.evaluated) return null;
  const evaluated = lastRun.evaluated ?? (
    (lastRun.qualified || 0) + (lastRun.needs_review || 0) + (lastRun.disqualified || 0)
  );
  return (
    <div className="mt-3 rounded-xl border border-indigo-100 bg-indigo-50/40 px-4 py-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-[13px] font-semibold text-indigo-800">Last run</span>
        <span className="text-[12px] text-indigo-600">{formatDateTime(lastRun.at)}</span>
        {lastRun.cancelled && (
          <span className="rounded-md bg-rose-100 px-2 py-0.5 text-[11px] font-medium text-rose-700">Cancelled</span>
        )}
      </div>
      <div className="mt-1.5 flex flex-wrap gap-x-4 gap-y-1 text-[12.5px] text-indigo-700">
        <span><span className="font-semibold tabular-nums">{evaluated}</span> evaluated</span>
        <span><span className="font-semibold tabular-nums text-emerald-700">{lastRun.qualified || 0}</span> qualified</span>
        <span><span className="font-semibold tabular-nums text-amber-700">{lastRun.needs_review || 0}</span> needs review</span>
        <span><span className="font-semibold tabular-nums text-zinc-500">{lastRun.disqualified || 0}</span> disqualified</span>
        {lastRun.cost_usd != null && (
          <span className="tabular-nums">${Number(lastRun.cost_usd).toFixed(2)} charged</span>
        )}
      </div>
      {entries.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-2">
          {entries.map(([src, c]) => {
            const n = (c.qualified || 0) + (c.needs_review || 0) + (c.disqualified || 0);
            if (!n && !c.error) return null;
            return (
              <span key={src} className="rounded-lg bg-white/80 px-2.5 py-1 text-[11.5px] text-indigo-700 ring-1 ring-inset ring-indigo-100">
                {src}: {n || '0'}
                {c.qualified ? ` · ${c.qualified}✓` : ''}
                {c.needs_review ? ` · ${c.needs_review}~` : ''}
                {c.disqualified ? ` · ${c.disqualified}✕` : ''}
                {c.error ? ' · error' : ''}
              </span>
            );
          })}
        </div>
      )}
    </div>
  );
}

const SIGNAL_LABELS = {
  job_posting: 'Hiring', layoff: 'Layoff', leadership_change: 'Leadership',
  acquisition: 'M&A', funding_round: 'Funding',
};

// ── RunActivityLog — persistent feed of recent evaluations (incl. disqualified) ─
function RunActivityLog({ items }) {
  if (!items || !items.length) return null;
  return (
    <div className="mt-6 overflow-hidden rounded-2xl border border-zinc-200 bg-white shadow-sm shadow-zinc-900/[0.02]">
      <div className="border-b border-zinc-100 px-5 py-3">
        <h2 className="text-[14px] font-semibold text-zinc-800">Recent evaluations</h2>
        <p className="mt-0.5 text-[12px] text-zinc-500">
          Everything the qualifier decided recently — including disqualified. Newest first.
        </p>
      </div>
      <div className="max-h-72 divide-y divide-zinc-50 overflow-y-auto">
        {items.map((item, i) => (
          <div key={`${item.company_key || item.name}-${item.at}-${i}`}
            className="flex items-start gap-3 px-5 py-2.5 hover:bg-zinc-50/60">
            <VerdictBadge status={item.status} />
            <div className="min-w-0 flex-1">
              <div className="truncate text-[13px] font-medium text-zinc-800">{item.name}</div>
              <div className="mt-0.5 flex flex-wrap items-center gap-1.5 text-[11.5px] text-zinc-400">
                <span>{formatDateTime(item.at)}</span>
                {item.signal_type && (
                  <>
                    <span className="text-zinc-300">·</span>
                    <span>{SIGNAL_LABELS[item.signal_type] || item.signal_type}</span>
                  </>
                )}
                {item.signal_summary && (
                  <>
                    <span className="text-zinc-300">·</span>
                    <span className="max-w-[240px] truncate">{item.signal_summary}</span>
                  </>
                )}
              </div>
            </div>
            {item.cost_usd != null && item.cost_usd > 0 && (
              <span className="shrink-0 text-[12px] tabular-nums text-zinc-500">${item.cost_usd.toFixed(2)}</span>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

// ── RunConfigPopover — the "Scan signals" control: scope, cost cap, and the
// social-listening setup in one surface. One Run, one popover. The qualifier
// costs ~$0.12/company, so worst-case spend is (sources × cap), shown before
// you commit. "All signals" also mines the monitored LinkedIn accounts + event
// keywords; "Jobs only" is the cheap focused pull.
function RunConfigPopover({ scope, onScope, limit, onLimit, social, onManageSocial, onRun, onClose }) {
  const n = Number(limit) > 0 ? Number(limit) : 0;
  const sources = scope === 'jobs' ? 1 : 4;
  const effective = n || DEFAULT_RUN_LIMIT;     // blank → safe default, never "no cap"
  const estCompanies = effective * sources;
  const estCost = (estCompanies * 0.12).toFixed(2);
  const includesSocial = scope === 'all';
  const acc = (social && social.accounts) || 0;
  const kw = (social && social.keywords) || 0;
  return (
    <div className="absolute right-0 top-full z-40 mt-2 w-80 rounded-xl border border-zinc-200 bg-white p-4 shadow-xl shadow-zinc-900/10">
      <div className="text-[13px] font-semibold text-zinc-800">Scan signals</div>
      <p className="mt-0.5 text-[11.5px] leading-relaxed text-zinc-500">
        Pulls the last 24h of buying signals: hiring, leadership, M&A and funding
        {includesSocial ? ', plus LinkedIn engagement and event attendees' : ''}. Layoffs run on the nightly cron.
      </p>
      <label className="mt-3 block text-[12px] font-medium text-zinc-600">Sources</label>
      <div className="mt-1 grid grid-cols-2 gap-1.5">
        {[['jobs', 'Jobs only'], ['all', 'All signals']].map(([v, lbl]) => (
          <button key={v} onClick={() => onScope(v)}
            className={`rounded-lg px-2.5 py-1.5 text-[12.5px] font-medium ring-1 ring-inset transition-colors ${scope === v ? 'bg-indigo-600 text-white ring-indigo-600' : 'bg-white text-zinc-600 ring-zinc-200 hover:bg-zinc-50'}`}>
            {lbl}
          </button>
        ))}
      </div>
      <label className="mt-3 block text-[12px] font-medium text-zinc-600">
        Companies per source (cost cap)
      </label>
      <input type="number" min="1" max="500" value={limit}
        onChange={(e) => onLimit(e.target.value)}
        className="mt-1 w-full rounded-lg border border-zinc-200 px-3 py-1.5 text-[13px] text-zinc-800 focus:border-zinc-300 focus:outline-none focus-visible:ring-2 focus-visible:ring-zinc-200" />
      <div className="mt-2 rounded-lg bg-zinc-50 px-3 py-2 text-[11.5px] text-zinc-500">
        Up to <span className="font-semibold text-zinc-700">{estCompanies}</span> companies · est <span className="font-semibold text-zinc-700">${estCost}</span>
        {n === 0 && <span className="text-zinc-400"> · blank uses {DEFAULT_RUN_LIMIT}/source</span>}
      </div>
      {/* Social listening — the setup the "All signals" scan mines. */}
      <div className="mt-3 overflow-hidden rounded-lg border border-zinc-200">
        <div className="flex items-center justify-between gap-2 px-3 py-2">
          <span className="inline-flex items-center gap-1.5 text-[12px] font-medium text-zinc-700">
            <Icons.leadership className="h-3.5 w-3.5 text-indigo-500" />Social listening
          </span>
          <button onClick={onManageSocial}
            className="text-[12px] font-medium text-indigo-600 transition-colors hover:text-indigo-700">Manage</button>
        </div>
        <div className="border-t border-zinc-100 bg-zinc-50/50 px-3 py-1.5 text-[11.5px] text-zinc-400">
          {acc + kw === 0
            ? 'No accounts or event keywords yet'
            : `${acc} ${acc === 1 ? 'account' : 'accounts'} · ${kw} ${kw === 1 ? 'keyword' : 'keywords'}`}
          {!includesSocial && acc + kw > 0 && <span> · included with All signals</span>}
        </div>
      </div>
      <div className="mt-3 flex items-center justify-end gap-2">
        <button onClick={onClose} className="rounded-lg px-3 py-1.5 text-[12.5px] font-medium text-zinc-500 transition-colors hover:bg-zinc-100">Cancel</button>
        <button onClick={onRun} className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-3 py-1.5 text-[12.5px] font-medium text-white transition-colors hover:bg-indigo-700">
          <Icons.zap className="h-3.5 w-3.5" />Scan
        </button>
      </div>
    </div>
  );
}

// ── ConfirmDeleteModal — guard a destructive bulk delete ─────────────────────
function ConfirmDeleteModal({ count, onCancel, onConfirm }) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-zinc-900/30 backdrop-blur-[2px] animate-fade" onClick={onCancel} />
      <div className="relative w-full max-w-md rounded-2xl border border-zinc-200 bg-white p-6 shadow-xl animate-pop">
        <h3 className="text-[16px] font-semibold text-zinc-900">
          Delete {count} {count === 1 ? 'company' : 'companies'}?
        </h3>
        <p className="mt-1 text-[13px] leading-relaxed text-zinc-500">
          This removes them and their signals from Discovery. They leave the dedup
          ledger too, so they can be re-discovered (and re-qualified) on a later run.
        </p>
        <div className="mt-5 flex items-center justify-end gap-2">
          <button onClick={onCancel}
            className="rounded-lg px-3.5 py-2 text-[13px] font-medium text-zinc-500 transition-colors hover:bg-zinc-100 hover:text-zinc-700">
            Cancel
          </button>
          <button onClick={onConfirm}
            className="inline-flex items-center gap-1.5 rounded-lg bg-rose-600 px-3.5 py-2 text-[13px] font-medium text-white transition-colors hover:bg-rose-700">
            <Icons.trash className="h-4 w-4" />Delete
          </button>
        </div>
      </div>
    </div>
  );
}

// ── WatchStrip — subtle "watching N single-RCM-role companies" banner ────────
// The jobs stacking gate parks any company with only ONE open standard RCM role
// (not enough to spend a qualify on). They're not lost — re-checked every run
// and auto-qualified the moment a second role opens. Kept deliberately quiet: a
// one-line strip that expands to a compact list, so it informs without noise.
function WatchStrip({ parked }) {
  const [open, setOpen] = useState(false);
  if (!parked || !parked.count) return null;
  const { count, companies = [], stack_min = 2 } = parked;
  return (
    <div className="border-b border-zinc-100 bg-zinc-50/70">
      <button onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2.5 px-6 py-2.5 text-left text-[13px] text-zinc-500 transition-colors hover:bg-zinc-100/70">
        <Icons.clock className="h-4 w-4 shrink-0 text-zinc-400" />
        <span>
          Watching <span className="font-semibold tabular-nums text-zinc-700">{count}</span>{' '}
          {count === 1 ? 'company' : 'companies'} with a single open RCM role — they
          auto-qualify the moment {stack_min <= 2 ? 'a second' : 'another'} role opens.
        </span>
        <Icons.chevron className={`ml-auto h-4 w-4 shrink-0 text-zinc-400 transition-transform ${open ? 'rotate-180' : ''}`} />
      </button>
      {open && (
        <div className="max-h-56 overflow-y-auto border-t border-zinc-100 px-6 py-1.5">
          <ul className="divide-y divide-zinc-100">
            {companies.map((c) => (
              <li key={c.company_key} className="flex items-center justify-between gap-3 py-1.5 text-[12.5px]">
                <span className="min-w-0 truncate font-medium text-zinc-700">{c.name}</span>
                <span className="flex shrink-0 items-center gap-2 text-zinc-400">
                  {c.role && <span className="rounded bg-zinc-100 px-1.5 py-0.5 text-[11px] font-medium text-zinc-500">{c.role}</span>}
                  {c.state && <span>{c.state}</span>}
                  {c.sample_url && (
                    <a href={safeHref(c.sample_url)} target="_blank" rel="noreferrer"
                      className="text-zinc-400 transition-colors hover:text-indigo-600" title="View the open role">
                      <Icons.ext className="h-3.5 w-3.5" />
                    </a>
                  )}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

// ════════════════════════════════════════════════════════════════════════════
// App shell: Discovery | Scored. Discovery is unchanged; Scored is wired to the
// scoring API. Promote moves a company from Discovery into Scored.
// ════════════════════════════════════════════════════════════════════════════
function App() {
  const [view, setView] = useState('discovery');
  const [toasts, setToasts] = useState([]);
  const toastId = useRef(0);
  function pushToast(message, tone = 'success') {
    const id = ++toastId.current;
    setToasts((t) => [...t, { id, message, tone }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 2600);
  }

  // Rubric definitions are the single source of truth — load once so the Scored
  // components read window.FRAMEWORKS rather than hardcoding bands/pillars.
  useEffect(() => {
    window.API.frameworks().then((f) => { window.FRAMEWORKS = f; }).catch(() => {});
  }, []);

  // Cross-talk: when a company is promoted, nudge the Scored view to refetch and
  // pulse the nav badge so the transition reads.
  const [scoredRefreshKey, setScoredRefreshKey] = useState(0);
  const [scoredCount, setScoredCount] = useState(0);
  const [spend, setSpend] = useState(null);   // scoring stats rollup (for the discovery cost meter)
  const [navPulse, setNavPulse] = useState(false);
  function bumpScored() {
    setScoredRefreshKey((k) => k + 1);
    setNavPulse(true);
    setTimeout(() => setNavPulse(false), 1600);
  }
  // The nav badge count must be correct on either tab, so App owns it from the
  // lightweight stats endpoint rather than only while ScoredView is mounted.
  useEffect(() => {
    let alive = true;
    const load = () => window.API.scoringStats()
      .then((s) => { if (alive && s) { setScoredCount(s.scored_count); setSpend(s); } })
      .catch(() => {});
    load();
    const id = setInterval(load, 8000);
    return () => { alive = false; clearInterval(id); };
  }, [scoredRefreshKey]);

  // ── DISCOVERY state + logic (unchanged from the live panel) ─────────────────
  const [loading, setLoading] = useState(true);
  const [companies, setCompanies] = useState([]);
  const [stats, setStats] = useState({ panel_pending: 0, qualified: 0, needs_review: 0, total: 0 });
  const [segment, setSegment] = useState('all');
  const [signalType, setSignalType] = useState('all');
  const [abmFilter, setAbmFilter] = useState('all');       // 'all' | 'match' | 'confirmed'
  const [socialOpen, setSocialOpen] = useState(false);     // Monitored Accounts modal
  const [abmInfo, setAbmInfo] = useState(null);            // { total, uploaded_at, indexed }
  const [parked, setParked] = useState(null);             // jobs stacking watch list
  const [socialInfo, setSocialInfo] = useState(null);     // { accounts, keywords } for the Scan popover
  const abmInputRef = useRef(null);
  const [tab, setTab] = useState('qualified');
  const [openKey, setOpenKey] = useState(null);
  const [leaving, setLeaving] = useState({});
  const [rejectFor, setRejectFor] = useState(null);
  // Cost-controlled test-run config + live run state.
  const [runScope, setRunScope] = useState('jobs');        // 'jobs' | 'all'
  const [runLimit, setRunLimit] = useState(2);             // companies/source cap
  const [paused, setPaused] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  // Multi-select for bulk delete (set of company_key).
  const [selected, setSelected] = useState(() => new Set());
  const [confirmDelete, setConfirmDelete] = useState(false);

  const [autoEnabled, setAutoEnabled] = useState(false);
  const [scoreHour, setScoreHour] = useState(15);
  const [deadline, setDeadline] = useState(() => window.nextDeadline(15, Date.now()));
  const [now, setNow] = useState(Date.now());
  const [autoOpen, setAutoOpen] = useState(false);
  const stateRef = useRef({ companies: [], leaving: {} });
  stateRef.current = { companies, leaving };

  async function loadAll(soft = false) {
    if (!soft) setLoading(true);
    try {
      const [qualified, needsReview, s] = await Promise.all([
        window.API.panel({ status: 'qualified' }),
        window.API.panel({ status: 'needs_review' }),
        window.API.stats(),
      ]);
      const tagged = [
        ...qualified.map((c) => ({ ...c, bucket: 'qualified' })),
        ...needsReview.map((c) => ({ ...c, bucket: 'needs_review' })),
      ].sort((a, b) => new Date(b.qualified_at || b.first_seen_at) - new Date(a.qualified_at || a.first_seen_at));
      setCompanies(tagged);
      setStats(s);
      // Best-effort: the stacking watch list (parked single-standard companies).
      window.API.parked().then(setParked).catch(() => {});
    } catch (e) {
      if (!soft) pushToast(`Couldn't load: ${e.message}`, 'danger');
    } finally {
      if (!soft) setLoading(false);
    }
  }
  useEffect(() => { loadAll(); }, []);
  useEffect(() => { window.API.abmSummary().then(setAbmInfo).catch(() => {}); }, []);
  useEffect(() => { loadSocialInfo(); }, []);

  // Counts for the Scan popover's social-listening summary; refreshed whenever
  // the setup modal closes so an added account/keyword reflects immediately.
  function loadSocialInfo() {
    Promise.all([
      window.API.socialTargets().catch(() => ({ targets: [] })),
      window.API.eventKeywords().catch(() => ({ keywords: [] })),
    ]).then(([t, k]) => setSocialInfo({
      accounts: (t.targets || []).filter((x) => x.active !== false).length,
      keywords: (k.keywords || []).filter((x) => x.active !== false).length,
    })).catch(() => {});
  }

  async function handleAbmUpload(file) {
    if (!file) return;
    pushToast(`Uploading ${file.name}…`, 'muted');
    try {
      const res = await window.API.importAbm(file);
      pushToast(`ABM list loaded — ${res.stored.toLocaleString()} target accounts`, 'success');
      await window.API.abmSummary().then(setAbmInfo).catch(() => {});
      await loadAll(true);   // re-annotate the panel against the new list
    } catch (e) {
      pushToast(`Upload failed: ${e.message}`, 'danger');
    }
  }

  const [activity, setActivity] = useState([]);
  const [lastRun, setLastRun] = useState(null);
  const [runLog, setRunLog] = useState([]);
  const [discoRunning, setDiscoRunning] = useState(false);   // on-demand run in flight
  const [runPhase, setRunPhase] = useState(null);            // which run is live (label)
  const [confirmRun, setConfirmRun] = useState(false);
  const wasActiveRef = useRef(false);
  // Timestamp of the last pause/resume/cancel click, so the 4s poll doesn't
  // stomp the optimistic UI before the backend reflects the action.
  const controlActionAt = useRef(0);
  useEffect(() => {
    let alive = true;
    async function poll() {
      try {
        const a = await window.API.activity();
        if (!alive) return;
        const active = (a && a.active) || [];
        setActivity(active);
        setLastRun((a && a.last_run) || null);
        setRunLog((a && a.recent) || []);
        // The live coroutine (a.running) is the source of truth for "a run is
        // active" — NOT stale connector_runs rows, which can linger after a
        // crash/restart and show a phantom in-progress run.
        const running = !!(a && a.running);
        setDiscoRunning(running);
        setRunPhase(running ? ((a && a.phase) || null) : null);
        if (Date.now() - controlActionAt.current > 5000) {
          setPaused(!!(a && a.paused));
          setCancelling(!!(a && a.cancelling));
        }
        if (!running) { setPaused(false); setCancelling(false); }
        // Refresh the panel while ANY run is live — a social scan produces no
        // connector_runs (active stays []), so keying off `running` is what makes
        // its qualified companies stream in live (not just discovery runs).
        if (running) { wasActiveRef.current = true; loadAll(true); }
        else if (wasActiveRef.current) {
          wasActiveRef.current = false;
          loadAll(true);
          window.API.scoringStats().then((s) => { if (alive && s) setSpend(s); }).catch(() => {});
          const lr = a && a.last_run;
          const sr = a && a.last_social;
          const n = lr && (lr.evaluated ?? ((lr.qualified || 0) + (lr.needs_review || 0) + (lr.disqualified || 0)));
          if (n != null) {
            pushToast(`Run complete — ${n} evaluated, $${(lr.cost_usd || 0).toFixed(2)} charged`, 'success');
          } else if (sr) {
            pushToast(`Scan complete — ${sr.qualified || 0} qualified, ${sr.enriched || 0} enriched`, 'success');
          } else {
            pushToast('Run complete', 'success');
          }
        }
      } catch (_) { /* ignore */ }
    }
    poll();
    const id = setInterval(poll, 4000);
    return () => { alive = false; clearInterval(id); };
  }, []);

  async function handleRunDiscovery() {
    setConfirmRun(false);
    setDiscoRunning(true);
    // Never send an empty/zero limit — that would be an unlimited (expensive)
    // run. Fall back to the default cap so a manual run is always bounded.
    const lim = Number(runLimit) > 0 ? Math.floor(Number(runLimit)) : DEFAULT_RUN_LIMIT;
    const body = { limit: lim };
    // Jobs-only is the cheap focused pull (no social); All signals scans the
    // connectors + LinkedIn engagement + event attendees in one run.
    if (runScope === 'jobs') { body.sources = ['jobs']; body.include_social = false; }
    else body.include_social = true;
    try {
      const res = await window.API.runDiscovery(body);
      if (res && res.busy) { setDiscoRunning(false); pushToast('A discovery run is already in progress.', 'muted'); return; }
      if (res && res.budget_blocked) {
        setDiscoRunning(false);
        pushToast(`Discovery budget reached ($${res.month_discovery_cost} of $${res.discovery_budget}). Raise DISCOVERY_MONTHLY_BUDGET or wait.`, 'danger');
        return;
      }
      const scopeLabel = body.sources ? 'jobs only' : 'all signals';
      const capLabel = body.limit ? `, ${body.limit}/source` : '';
      pushToast(`Discovery running — ${scopeLabel}${capLabel}…`, 'success');
    } catch (e) { setDiscoRunning(false); pushToast(`Couldn't start: ${e.message}`, 'danger'); }
  }

  async function handlePauseResume() {
    controlActionAt.current = Date.now();
    try {
      if (paused) { await window.API.resumeDiscovery(); setPaused(false); pushToast('Resumed', 'success'); }
      else { await window.API.pauseDiscovery(); setPaused(true); pushToast('Paused — spend frozen', 'muted'); }
    } catch (e) { pushToast(`Couldn't ${paused ? 'resume' : 'pause'}: ${e.message}`, 'danger'); }
  }

  async function handleCancelRun() {
    controlActionAt.current = Date.now();
    setCancelling(true);
    try { await window.API.cancelDiscovery(); pushToast('Cancelling — finishing the current company, then stopping.', 'muted'); }
    catch (e) { setCancelling(false); pushToast(`Couldn't cancel: ${e.message}`, 'danger'); }
  }

  function toggleSelect(key) {
    setSelected((prev) => {
      const n = new Set(prev);
      if (n.has(key)) n.delete(key); else n.add(key);
      return n;
    });
  }

  async function handleDeleteSelected() {
    const keys = [...selected];
    if (!keys.length) return;
    setConfirmDelete(false);
    keys.forEach((k) => setLeaving((l) => ({ ...l, [k]: true })));
    try {
      const res = await window.API.deleteCompanies({ keys });
      setSelected(new Set());
      setTimeout(() => loadAll(true), 320);
      pushToast(`Deleted ${res.deleted} ${res.deleted === 1 ? 'company' : 'companies'}`, 'success');
    } catch (e) {
      keys.forEach((k) => setLeaving((l) => { const n = { ...l }; delete n[k]; return n; }));
      pushToast(`Delete failed: ${e.message}`, 'danger');
    }
  }

  function removeCompany(key) {
    setLeaving((l) => ({ ...l, [key]: true }));
    setTimeout(() => {
      setCompanies((cs) => cs.filter((c) => c.company_key !== key));
      setStats((s) => ({ ...s, panel_pending: Math.max(0, s.panel_pending - 1) }));
      setLeaving((l) => { const n = { ...l }; delete n[key]; return n; });
    }, 320);
  }

  async function handlePromote(key) {
    const c = companies.find((x) => x.company_key === key);
    if (openKey === key) setOpenKey(null);
    try {
      await window.API.promote(key);
      removeCompany(key);
      bumpScored();
      pushToast(`Promoted ${c ? c.name : 'company'} → Scoring`, 'success');
    } catch (e) { pushToast(`Promote failed: ${e.message}`, 'danger'); }
  }
  async function handleDefer(key) {
    const c = companies.find((x) => x.company_key === key);
    if (openKey === key) setOpenKey(null);
    try { await window.API.defer(key); removeCompany(key); pushToast(`Deferred ${c ? c.name : 'company'}`, 'muted'); }
    catch (e) { pushToast(`Defer failed: ${e.message}`, 'danger'); }
  }
  async function handleRestore(key) {
    const c = companies.find((x) => x.company_key === key);
    if (openKey === key) setOpenKey(null);
    setLeaving((l) => ({ ...l, [key]: true }));
    try {
      await window.API.restore(key);
      pushToast(`Restored ${c ? c.name : 'company'} to queue`, 'success');
      setTimeout(() => { loadAll(true); setLeaving((l) => { const n = { ...l }; delete n[key]; return n; }); }, 320);
    } catch (e) { setLeaving((l) => { const n = { ...l }; delete n[key]; return n; }); pushToast(`Restore failed: ${e.message}`, 'danger'); }
  }
  async function handleReject(key, reason) {
    const c = companies.find((x) => x.company_key === key);
    if (openKey === key) setOpenKey(null);
    setRejectFor(null);
    try { await window.API.reject(key, reason); removeCompany(key); pushToast(`Rejected ${c ? c.name : 'company'} · ${reason}`, 'danger'); }
    catch (e) { pushToast(`Reject failed: ${e.message}`, 'danger'); }
  }

  async function doAutoScore() {
    const { companies: cs, leaving: lv } = stateRef.current;
    const remaining = cs.filter((c) => c.bucket === 'qualified' && !lv[c.company_key]);
    if (remaining.length === 0) return;
    setOpenKey(null); setRejectFor(null);
    for (const c of remaining) {
      try { await window.API.promote(c.company_key); removeCompany(c.company_key); } catch (e) { /* leave it */ }
    }
    bumpScored();
    pushToast(`${remaining.length} ${remaining.length === 1 ? 'company' : 'companies'} promoted to Scoring`, 'success');
  }
  useEffect(() => {
    if (!autoEnabled) return;
    let fired = false;
    const id = setInterval(() => {
      const t = Date.now(); setNow(t);
      if (!fired && t >= deadline) { fired = true; doAutoScore(); setDeadline(window.nextDeadline(scoreHour, t + 1000)); }
    }, 200);
    return () => clearInterval(id);
  }, [autoEnabled, deadline, scoreHour]);

  const filtered = useMemo(() => companies.filter((c) => {
    if (c.bucket !== tab) return false;
    if (segment !== 'all' && c.segment !== segment) return false;
    if (signalType !== 'all' && !c.signals.some((s) => s.signal_type === signalType)) return false;
    if (abmFilter === 'match' && !c.abm_match) return false;
    if (abmFilter === 'confirmed' && !(c.abm_match && c.abm_match.tier === 'confirmed')) return false;
    return true;
  }), [companies, tab, segment, signalType, abmFilter]);

  const openCompany = companies.find((c) => c.company_key === openKey) || null;
  const visibleRows = filtered.filter((c) => !leaving[c.company_key]);
  const visibleCount = visibleRows.length;
  const visibleKeys = visibleRows.map((c) => c.company_key);
  const selectedVisible = visibleKeys.filter((k) => selected.has(k)).length;
  const allSelected = visibleCount > 0 && selectedVisible === visibleCount;
  function toggleSelectAll() {
    setSelected((prev) => {
      const n = new Set(prev);
      if (allSelected) visibleKeys.forEach((k) => n.delete(k));
      else visibleKeys.forEach((k) => n.add(k));
      return n;
    });
  }
  const qualifiedCount = companies.filter((c) => c.bucket === 'qualified' && !leaving[c.company_key]).length;
  const needsCount = companies.filter((c) => c.bucket === 'needs_review' && !leaving[c.company_key]).length;
  const abmMatchCount = companies.filter((c) => c.bucket === tab && c.abm_match && !leaving[c.company_key]).length;
  const remainingMs = deadline - now;
  const queuedCount = qualifiedCount;
  const urgent = autoEnabled && remainingMs <= 10 * 60 * 1000 && queuedCount > 0;
  function changeHour(h) { setScoreHour(h); setDeadline(window.nextDeadline(h, Date.now())); }
  function previewCountdown() { setAutoEnabled(true); setDeadline(Date.now() + 12000); setAutoOpen(false); }

  const discovery = view === 'discovery';

  return (
    <div className="min-h-screen bg-[#fafafa] text-zinc-900">
      <header className="sticky top-0 z-30 border-b border-zinc-200/80 bg-[#fafafa]/85 backdrop-blur-md">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-8 py-4">
          <div className="flex items-center gap-3">
            <div className="flex items-center gap-2.5">
              <img src={(typeof window !== 'undefined' && window.MAGICAL_LOGO_URL) || 'assets/magical-logo.svg'}
                alt="Magical" className="h-7 w-7 rounded-lg"
                onError={(e) => { e.currentTarget.style.display = 'none'; }} />
              <span className="text-[15px] font-semibold tracking-tight">Magical</span>
              <span className="text-zinc-300">/</span>
            </div>
            <NavSwitch view={view} onChange={setView} scoredCount={scoredCount} pulse={navPulse} />
          </div>
          <div className="flex items-center gap-3">
            {discovery ? (
              <>
                <span className="hidden text-[12px] lg:inline">
                  {activity.length > 0 ? (
                    <span className="inline-flex items-center gap-1.5 font-medium text-indigo-600">
                      <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-indigo-500"></span>Processing…
                    </span>
                  ) : (<span className="text-zinc-400">Live · {stats.total} surfaced</span>)}
                </span>
                <div className="relative">
                  <AutoScorePill enabled={autoEnabled} remainingMs={remainingMs} active={autoOpen} onClick={() => setAutoOpen((o) => !o)} />
                  {autoOpen && (
                    <AutoScorePopover enabled={autoEnabled} onToggle={setAutoEnabled} hour={scoreHour} onHour={changeHour}
                      deadline={deadline} queued={queuedCount} onPreview={previewCountdown} onClose={() => setAutoOpen(false)} />
                  )}
                </div>
                {discoRunning ? (
                  <span className="inline-flex items-center gap-1.5">
                    <button onClick={handlePauseResume} disabled={cancelling}
                      title={paused ? 'Resume the run from where it paused' : 'Pause the run — no new qualification starts, spend freezes'}
                      className="inline-flex items-center gap-1.5 rounded-lg border border-zinc-200 bg-white px-3 py-1.5 text-[13px] font-medium text-zinc-700 transition-colors hover:bg-zinc-50 disabled:cursor-not-allowed disabled:opacity-50">
                      {paused ? <><Icons.play className="h-4 w-4" />Resume</> : <><Icons.pause className="h-4 w-4" />Pause</>}
                    </button>
                    <button onClick={handleCancelRun} disabled={cancelling}
                      title="Stop the run cleanly at the next company boundary"
                      className="inline-flex items-center gap-1.5 rounded-lg border border-rose-200 bg-white px-3 py-1.5 text-[13px] font-medium text-rose-600 transition-colors hover:bg-rose-50 disabled:cursor-not-allowed disabled:opacity-50">
                      <Icons.x className="h-4 w-4" />{cancelling ? 'Stopping…' : 'Cancel'}
                    </button>
                  </span>
                ) : (
                  <div className="relative">
                    <button onClick={() => setConfirmRun((o) => !o)}
                      title="Scan all signal sources for the last 24h"
                      className="inline-flex items-center gap-2 rounded-lg bg-indigo-600 px-3 py-1.5 text-[13px] font-medium text-white shadow-sm transition-colors hover:bg-indigo-700">
                      <Icons.zap className="h-4 w-4" />Scan signals
                    </button>
                    {confirmRun && (
                      <RunConfigPopover
                        scope={runScope} onScope={setRunScope}
                        limit={runLimit} onLimit={setRunLimit}
                        social={socialInfo} onManageSocial={() => { setConfirmRun(false); setSocialOpen(true); }}
                        onRun={handleRunDiscovery} onClose={() => setConfirmRun(false)} />
                    )}
                  </div>
                )}
                <button onClick={() => { setLoading(true); loadAll(); pushToast('Refreshed', 'muted'); }}
                  className="inline-flex items-center gap-2 rounded-lg border border-zinc-200 bg-white px-3 py-1.5 text-[13px] font-medium text-zinc-600 transition-colors hover:bg-zinc-50">
                  <Icons.refresh className="h-4 w-4" />Refresh
                </button>
              </>
            ) : (
              <span className="hidden items-center gap-1.5 text-[12px] text-zinc-400 lg:inline-flex">
                <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />Live
              </span>
            )}
          </div>
        </div>
      </header>

      {discovery && (discoRunning || activity.length > 0) && <ActivityBanner runs={activity} paused={paused} cancelling={cancelling} phase={runPhase} />}

      {discovery ? (
        <main className="mx-auto max-w-6xl px-8 py-8">
          <div className="mb-6">
            <h1 className="text-[24px] font-semibold tracking-tight text-zinc-900">Discovery Panel</h1>
            <p className="mt-1 text-[14px] text-zinc-500">Review AI-qualified companies and route each one. Promote or reject.</p>
          </div>

          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <StatTile value={stats.panel_pending} label="In queue" emphasized />
            <StatTile value={stats.qualified} label="Qualified" />
            <StatTile value={stats.needs_review} label="Needs review" />
            <StatTile value={stats.disqualified} label="Disqualified (hidden)" />
          </div>

          <DiscoverySpendMeter spend={spend} lastRun={lastRun} />
          <LastRunSummary lastRun={lastRun} />

          <div className="mt-8 overflow-hidden rounded-2xl border border-zinc-200 bg-white shadow-sm shadow-zinc-900/[0.02]">
            <div className="flex items-center gap-1 border-b border-zinc-100 px-4 pt-1.5">
              <TabButton active={tab === 'qualified'} onClick={() => setTab('qualified')} label="Qualified" count={qualifiedCount} accent="indigo" />
              <TabButton active={tab === 'needs_review'} onClick={() => setTab('needs_review')} label="Needs review" count={needsCount} accent="amber" />
            </div>

            <div className="flex flex-wrap items-center justify-between gap-3 border-b border-zinc-100 px-6 py-3.5">
              <div className="flex flex-wrap items-center gap-4">
                <Dropdown label="Segment" value={segment} onChange={setSegment}
                  options={[{ value: 'all', label: 'All' }, { value: 'health_system', label: 'Health System' }, { value: 'specialty', label: 'Specialty' }, { value: 'payer', label: 'Payer' }]} />
                <Dropdown label="Signal" value={signalType} onChange={setSignalType}
                  options={[{ value: 'all', label: 'All' }, { value: 'job_posting', label: 'Hiring' }, { value: 'layoff', label: 'Layoff' }, { value: 'leadership_change', label: 'Leadership change' }, { value: 'acquisition', label: 'Acquisition' }, { value: 'funding_round', label: 'Funding' }, { value: 'social_engagement', label: 'Engaged' }, { value: 'event_attendance', label: 'Event' }]} />
                <Dropdown label="ABM list" value={abmFilter} onChange={setAbmFilter}
                  options={[{ value: 'all', label: 'All' }, { value: 'match', label: `On ABM list${abmMatchCount ? ` (${abmMatchCount})` : ''}` }, { value: 'confirmed', label: 'ABM confirmed' }]} />
              </div>
              <div className="flex items-center gap-3">
                <input ref={abmInputRef} type="file" accept=".xlsx" className="hidden"
                  onChange={(e) => { const f = e.target.files[0]; e.target.value = ''; handleAbmUpload(f); }} />
                <button onClick={() => abmInputRef.current && abmInputRef.current.click()}
                  title={abmInfo && abmInfo.total ? `${abmInfo.total.toLocaleString()} ABM targets loaded — click to replace` : 'Upload your ABM target list (.xlsx)'}
                  className="inline-flex items-center gap-1.5 rounded-lg border border-zinc-200 bg-white px-3 py-1.5 text-[12.5px] font-medium text-zinc-600 transition-colors hover:bg-zinc-50">
                  <Icons.sparkle className="h-3.5 w-3.5 text-amber-500" />
                  {abmInfo && abmInfo.total ? `ABM list · ${abmInfo.total.toLocaleString()}` : 'Upload ABM list'}
                </button>
                {!loading && visibleCount > 0 && (
                  <label className="inline-flex cursor-pointer items-center gap-1.5 text-[13px] text-zinc-500 select-none">
                    <input type="checkbox" checked={allSelected} onChange={toggleSelectAll}
                      className="h-3.5 w-3.5 rounded border-zinc-300 text-indigo-600 focus:ring-indigo-300" />
                    Select all
                  </label>
                )}
                <span className="text-[13px] text-zinc-400">
                  {loading ? 'Loading…' : `${visibleCount} ${visibleCount === 1 ? 'company' : 'companies'}`}
                  {!loading && abmMatchCount > 0 && (
                    <span className="ml-1.5 font-medium text-amber-600">· {abmMatchCount} on ABM list</span>
                  )}
                </span>
              </div>
            </div>

            {selected.size > 0 && (
              <div className="flex items-center justify-between gap-3 border-b border-rose-100 bg-rose-50/60 px-6 py-2.5">
                <span className="text-[13px] font-medium text-rose-700">
                  {selected.size} selected
                </span>
                <div className="flex items-center gap-2">
                  <button onClick={() => setSelected(new Set())}
                    className="rounded-lg px-3 py-1.5 text-[12.5px] font-medium text-zinc-500 transition-colors hover:bg-white">
                    Clear
                  </button>
                  <button onClick={() => setConfirmDelete(true)}
                    className="inline-flex items-center gap-1.5 rounded-lg bg-rose-600 px-3 py-1.5 text-[12.5px] font-medium text-white transition-colors hover:bg-rose-700">
                    <Icons.trash className="h-3.5 w-3.5" />Delete {selected.size}
                  </button>
                </div>
              </div>
            )}

            {!loading && tab === 'needs_review' && visibleCount > 0 && (
              <div className="flex items-center gap-2.5 border-b border-zinc-100 bg-zinc-50/70 px-6 py-2.5 text-[13px] text-zinc-500">
                <Icons.info className="h-4 w-4 shrink-0 text-zinc-400" />
                <span>The AI wasn't confident enough to qualify or disqualify these — each needs your manual decision.</span>
              </div>
            )}

            {!loading && urgent && tab === 'qualified' && <AutoScoreBanner remainingMs={remainingMs} queued={queuedCount} />}

            {!loading && tab === 'qualified' && <WatchStrip parked={parked} />}

            {loading ? (
              <div className="animate-pulse">{Array.from({ length: 4 }).map((_, i) => <SkeletonRow key={i} />)}</div>
            ) : visibleCount === 0 ? (
              <EmptyState variant={tab} onRun={() => pushToast('Discovery runs on a schedule', 'muted')} />
            ) : (
              filtered.map((c) => (
                <CompanyRow key={c.company_key} company={c} leaving={!!leaving[c.company_key]}
                  selected={selected.has(c.company_key)}
                  onToggleSelect={() => toggleSelect(c.company_key)}
                  onOpen={() => setOpenKey(c.company_key)}
                  onPromote={() => handlePromote(c.company_key)}
                  onReject={() => setRejectFor(c)} />
              ))
            )}
          </div>

          <RunActivityLog items={runLog} />

          <p className="mt-4 text-center text-[12px] text-zinc-400">
            Sorted by most recently evaluated · Disqualified rows appear in Recent evaluations below
          </p>
        </main>
      ) : view === 'news' ? (
        <NewsView pushToast={pushToast} />
      ) : (
        <ScoredView refreshKey={scoredRefreshKey} pushToast={pushToast} onCount={setScoredCount} />
      )}

      {discovery && (
        <CompanyDrawer company={openCompany} onClose={() => setOpenKey(null)}
          onPromote={() => handlePromote(openKey)} onDefer={() => handleDefer(openKey)} onReject={() => setRejectFor(openCompany)}
          onRestore={() => handleRestore(openKey)} />
      )}
      {discovery && rejectFor && (
        <RejectReasonModal company={rejectFor} onCancel={() => setRejectFor(null)} onConfirm={(reason) => handleReject(rejectFor.company_key, reason)} />
      )}
      {discovery && confirmDelete && (
        <ConfirmDeleteModal count={selected.size}
          onCancel={() => setConfirmDelete(false)} onConfirm={handleDeleteSelected} />
      )}
      {socialOpen && <SocialMonitor onClose={() => { setSocialOpen(false); loadSocialInfo(); }} pushToast={pushToast} />}
      <ToastStack toasts={toasts} />
    </div>
  );
}

// ════════════════════════════════════════════════════════════════════════════
// Scored view — accounts in the scoring phase, read from the API. Polls while
// any account is in flight so 'Scoring…' rows resolve to scores live.
// ════════════════════════════════════════════════════════════════════════════

// ── CSV export (client-side, from the already-loaded accounts) ───────────────
function csvCell(v) {
  let s = v == null ? '' : String(v);
  // Excel formula-injection guard: a cell starting =, +, -, @ (or a sneaky
  // tab/CR) executes as a formula when the export is opened in Excel/Sheets.
  if (/^[=+\-@\t\r]/.test(s)) s = `'${s}`;
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}
function segLabel(seg) {
  const m = window.SEGMENT_META && window.SEGMENT_META[seg];
  return (m && m.label) || seg || '';
}
function buildAccountsCsv(accounts) {
  const head = ['Account', 'Domain', 'Segment', 'Sub-segment', 'Source', 'Import',
    'Fit', 'Analyst Total', 'Official Total', 'Max', 'Firmographic', 'Technographic',
    'Business Intent', 'Recommendation', 'QA Status', 'QA Notes', 'Scored',
    'Cost (USD)', 'Key facts'];
  const lines = accounts.map((a) => {
    const tier = a.tier || window.tierFor(a.framework, a.total);
    const pillars = window.pillarsFor(a);
    const pill = (i) => (pillars[i] ? `${pillars[i].score}/${pillars[i].max}` : '');
    const facts = a.firmographics
      ? Object.entries(a.firmographics).map(([k, v]) => `${k}: ${v}`).join('; ') : '';
    const qa = a.qa || {};
    const analystTotal = (qa.applied && qa.analyst_total != null) ? qa.analyst_total : a.total;
    return [
      a.name, a.domain || '', segLabel(a.segment), a.sub_segment || '',
      a.source === 'csv' ? 'CSV import' : 'Discovery', a.import_label || '',
      window.fitWord(tier.band), analystTotal, a.total, a.max_total,
      pill(0), pill(1), pill(2),
      a.recommendation || '', qa.status || '', qa.notes || '',
      a.scored_at ? window.shortDate(a.scored_at) : '',
      a.cost_usd != null ? a.cost_usd : '', facts,
    ].map(csvCell).join(',');
  });
  return [head.join(','), ...lines].join('\n');
}
function downloadCsv(filename, csv) {
  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url; link.download = filename;
  document.body.appendChild(link); link.click(); link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

// Doubles as the color legend (so the ring colors are readable) and the fit
// distribution, in one quiet line — replacing the old stat-tile row.
function FitLegend({ counts }) {
  const meta = window.FIT_META || {};
  const items = [['high', 'High'], ['medium', 'Medium'], ['low', 'Low'], ['out', 'Not a fit']];
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[12px] text-zinc-500">
      {items.map(([k, label]) => (
        <span key={k} className="inline-flex items-center gap-1.5">
          <span className={`h-1.5 w-1.5 rounded-full ${(meta[k] || {}).dot || 'bg-zinc-300'}`} />
          {label}<span className="tabular-nums text-zinc-400">{counts[k] || 0}</span>
        </span>
      ))}
    </div>
  );
}

function ScoredView({ refreshKey, pushToast, onCount }) {
  const [accounts, setAccounts] = useState([]);
  const [stats, setStats] = useState(null);
  const [loading, setLoading] = useState(true);
  const [segF, setSegF] = useState('all');
  const [fitF, setFitF] = useState('all');
  const [sourceF, setSourceF] = useState('all');
  const [dateF, setDateF] = useState('all');
  const [importF, setImportF] = useState('all');
  const [imports, setImports] = useState([]);
  const [confirmReset, setConfirmReset] = useState(false);
  const [confirmIntros, setConfirmIntros] = useState(false);
  const [selected, setSelected] = useState(() => new Set());   // row-selection for export
  const [openAcc, setOpenAcc] = useState(null);
  const [openLanding, setOpenLanding] = useState(null);
  const [importing, setImporting] = useState(false);
  const [batchKick, setBatchKick] = useState(false);   // optimistic "batch starting"

  async function load(soft = false) {
    try {
      // Accounts are primary; the spend summary + import list are best-effort so
      // the table still loads if either endpoint hiccups.
      const [a, s, im] = await Promise.all([
        window.API.scored(),
        window.API.scoringStats().catch(() => null),
        window.API.scoringImports().catch(() => null),
      ]);
      setAccounts(a);
      if (s) setStats(s);
      if (im) setImports(im.imports || []);
    } catch (e) { if (!soft) pushToast(`Couldn't load scores: ${e.message}`, 'danger'); }
    finally { if (!soft) setLoading(false); }
  }
  useEffect(() => { load(); }, []);
  useEffect(() => { if (refreshKey) load(true); }, [refreshKey]);
  // Poll while anything is scoring (or a queued batch is running) so rows + the
  // cost meter resolve live — and do one final refetch when the last in-flight
  // account finishes, so the resolved score lands without a manual reload.
  const wasActiveRef = useRef(false);
  const overheatRef = useRef(null);
  useEffect(() => {
    let alive = true;
    async function poll() {
      try {
        const [r, s] = await Promise.all([
          window.API.scoringActivity(),
          window.API.scoringStats().catch(() => null),
        ]);
        if (!alive) return;
        if (s) {
          setStats(s);
          if (!s.batch_running) setBatchKick(false);
          // Surface a batch that was stopped for overheating, once.
          const oh = s.last_overheat;
          const key = oh ? `${oh.actual}/${oh.estimated}` : null;
          if (key && key !== overheatRef.current) {
            overheatRef.current = key;
            pushToast(`Batch stopped — overheated (spent $${oh.actual}, est $${oh.estimated}).`, 'danger');
          } else if (!key) { overheatRef.current = null; }
        }
        const busy = (r.active || []).length > 0 || (s && s.batch_running);
        if (busy) { wasActiveRef.current = true; load(true); }
        else if (wasActiveRef.current) { wasActiveRef.current = false; load(true); }
      } catch (_) { /* ignore */ }
    }
    const id = setInterval(poll, 3000);
    poll();
    return () => { alive = false; clearInterval(id); };
  }, []);
  useEffect(() => { onCount(accounts.filter((a) => a.state === 'scored').length); }, [accounts]);

  async function handleScore(id) {
    const a = accounts.find((x) => x.account_id === id);
    setAccounts((prev) => prev.map((x) => (x.account_id === id ? { ...x, state: 'scoring' } : x)));
    pushToast(`Scoring ${a ? a.name : 'account'}…`, 'success');
    try { await window.API.scoreAccount(id); } catch (e) { pushToast(`Score failed: ${e.message}`, 'danger'); load(true); }
  }
  function handleImported(res) {
    setImporting(false);
    const n = res.imported;
    pushToast(`Imported ${n} ${n === 1 ? 'account' : 'accounts'} to the queue. Score them when ready.`, 'success');
    load(true);
  }

  async function handleScoreAll(limit) {
    const queued = accounts.filter((a) => a.state === 'queued');
    if (!queued.length) return;
    const n = limit ? Math.min(limit, queued.length) : queued.length;
    setBatchKick(true);
    pushToast(`Scoring ${n} queued ${n === 1 ? 'account' : 'accounts'}…`, 'success');
    // Clicking "Score all" through the confirm IS the large-spend confirmation,
    // so pass it through; the server still hard-caps to the monthly budget.
    const body = limit ? { limit } : {};
    body.confirm_large_spend = true;
    try {
      const res = await window.API.scoreQueued(body);
      if (res && res.budget_blocked) { setBatchKick(false); pushToast('Monthly budget reached — nothing scored. Raise the budget or wait.', 'danger'); }
      else if (res && res.started === 0) { setBatchKick(false); pushToast('A batch is already running.', 'success'); }
      else if (res && res.budget_capped) { pushToast(`Scoring ${res.started} that fit the budget (the rest stay queued).`, 'success'); }
    } catch (e) { setBatchKick(false); pushToast(`Couldn't start batch: ${e.message}`, 'danger'); }
    wasActiveRef.current = true;
    load(true);
  }

  async function handleReset() {
    setConfirmReset(false);
    setSelected(new Set());
    try {
      const res = await window.API.resetScores();
      if (res && res.busy) { pushToast('Finish the running batch first.', 'success'); return; }
      pushToast(`Cleared ${res.reset} ${res.reset === 1 ? 'score' : 'scores'} back to the queue.`, 'success');
      load(true);
    } catch (e) { pushToast(`Couldn't reset: ${e.message}`, 'danger'); }
  }

  async function handleRunAllIntros() {
    setConfirmIntros(false);
    try {
      const res = await window.API.runAllWarmIntros();
      if (!res || !res.scheduled) { pushToast('All scored accounts already have intros.', 'success'); return; }
      pushToast(`Finding intros for ${res.scheduled} ${res.scheduled === 1 ? 'account' : 'accounts'}…`, 'success');
      load(true);
    } catch (e) { pushToast(`Couldn't start: ${e.message}`, 'danger'); }
  }

  const bandOf = (a) => (a.total != null ? window.tierFor(a.framework, a.total).band : null);
  const withinDate = (iso, key) => {
    if (key === 'all') return true;
    if (!iso) return false;
    const days = key === 'today' ? 1 : key === '7d' ? 7 : 30;
    return (Date.now() - new Date(iso).getTime()) <= days * 86400000;
  };

  const scoredList = useMemo(() => {
    const arr = accounts.filter((a) => {
      if (segF !== 'all' && a.segment !== segF) return false;
      if (sourceF !== 'all' && a.source !== sourceF) return false;
      if (importF !== 'all' && a.import_label !== importF) return false;
      if (fitF !== 'all') { if (a.state !== 'scored' || bandOf(a) !== fitF) return false; }
      if (dateF !== 'all') { if (a.state !== 'scored' || !withinDate(a.scored_at, dateF)) return false; }
      return true;
    });
    const order = { scoring: 0, queued: 1, scored: 2, error: 3 };
    return arr.sort((a, b) => {
      if (order[a.state] !== order[b.state]) return order[a.state] - order[b.state];
      const ra = a.total != null ? a.total / a.max_total : 0;
      const rb = b.total != null ? b.total / b.max_total : 0;
      return rb - ra;
    });
  }, [accounts, segF, fitF, sourceF, dateF, importF]);

  function toggleSelect(id) {
    setSelected((prev) => {
      const n = new Set(prev);
      if (n.has(id)) n.delete(id); else n.add(id);
      return n;
    });
  }

  function handleExport() {
    const useSel = selected.size > 0;
    const rows = useSel
      ? accounts.filter((a) => a.state === 'scored' && selected.has(a.account_id))
      : scoredList.filter((a) => a.state === 'scored');
    if (!rows.length) { pushToast('No scored accounts to export.', 'success'); return; }
    const tag = useSel ? 'selected' : importF !== 'all' ? 'import' : sourceF !== 'all' ? sourceF : 'all';
    const stamp = new Date().toISOString().slice(0, 10);
    downloadCsv(`magical-scored-${tag}-${stamp}.csv`, buildAccountsCsv(rows));
    pushToast(`Exported ${rows.length} ${rows.length === 1 ? 'account' : 'accounts'} to CSV.`, 'success');
  }

  const scoredOnly = accounts.filter((a) => a.state === 'scored');
  const queuedCount = accounts.filter((a) => a.state === 'queued').length;
  const batchRunning = batchKick || !!(stats && stats.batch_running);
  const fitCounts = { high: 0, medium: 0, low: 0, out: 0 };
  scoredOnly.forEach((a) => { const b = bandOf(a); if (b in fitCounts) fitCounts[b] += 1; });
  // Warm-intros backfill: accounts still without intros (Apollo is free for all);
  // green/yellow also get paid school enrichment (~$9/1k profiles, ≤8/account —
  // mirrors the server estimate, display-only since the budget guard is server-side).
  const introTodo = scoredOnly.filter((a) => !['ready', 'generating'].includes((a.warm_intros || {}).state));
  const introGY = introTodo.filter((a) => ['high', 'medium'].includes(bandOf(a))).length;
  const introCost = introGY * 8 * 0.009;

  const filteredScoredIds = scoredList.filter((a) => a.state === 'scored').map((a) => a.account_id);
  const allFilteredSelected = filteredScoredIds.length > 0 && filteredScoredIds.every((id) => selected.has(id));
  function toggleSelectAll() {
    setSelected((prev) => {
      if (allFilteredSelected) { const n = new Set(prev); filteredScoredIds.forEach((id) => n.delete(id)); return n; }
      return new Set([...prev, ...filteredScoredIds]);
    });
  }

  const openAccount = accounts.find((a) => a.account_id === openAcc) || null;
  const landingAccount = accounts.find((a) => a.account_id === openLanding) || null;
  const visible = scoredList.length;

  return (
    <>
      <main className="mx-auto max-w-6xl px-8 py-8">
        <div className="mb-6 flex items-end justify-between gap-4">
          <div>
            <h1 className="text-[24px] font-semibold tracking-tight text-zinc-900">Scored accounts</h1>
            <p className="mt-1 text-[14px] text-zinc-500">One fit score per account, on its segment rubric. Open any row for the full breakdown.</p>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            {scoredOnly.length > 0 && !confirmReset && (
              <button onClick={handleExport}
                title={selected.size > 0 ? 'Download the selected accounts as CSV' : 'Download the accounts in the current view as CSV'}
                className="inline-flex items-center gap-1.5 rounded-lg border border-zinc-200 bg-white px-3 py-2 text-[13px] font-medium text-zinc-500 transition-colors hover:bg-zinc-50 hover:text-zinc-700">
                <Icons.download className="h-4 w-4" />Export{selected.size > 0 ? ` ${selected.size}` : ''}
              </button>
            )}
            {scoredOnly.length > 0 && introTodo.length > 0 && (confirmIntros ? (
              <span className="inline-flex items-center gap-1.5 rounded-lg border border-zinc-200 bg-white px-2 py-1.5 text-[12.5px]">
                <span className="px-1 text-zinc-500">Find intros for {introTodo.length}? {introGY > 0 ? `~$${introCost.toFixed(2)}` : 'free'}</span>
                <button onClick={() => setConfirmIntros(false)} className="rounded-md px-2 py-1 font-medium text-zinc-500 transition-colors hover:bg-zinc-100">Cancel</button>
                <button onClick={handleRunAllIntros} className="rounded-md bg-zinc-900 px-2.5 py-1 font-medium text-white transition-colors hover:bg-zinc-800">Run</button>
              </span>
            ) : (
              <button onClick={() => setConfirmIntros(true)} title="Find ICP decision-makers for every scored account (Apollo, free); green/yellow also get school enrichment for warm paths"
                className="inline-flex items-center gap-1.5 rounded-lg border border-zinc-200 bg-white px-3 py-2 text-[13px] font-medium text-zinc-500 transition-colors hover:bg-zinc-50 hover:text-zinc-700">
                <Icons.leadership className="h-4 w-4" />Find intros
              </button>
            ))}
            {scoredOnly.length > 0 && (confirmReset ? (
              <span className="inline-flex items-center gap-1.5 rounded-lg border border-zinc-200 bg-white px-2 py-1.5 text-[12.5px]">
                <span className="px-1 text-zinc-500">Clear all scores?</span>
                <button onClick={() => setConfirmReset(false)} className="rounded-md px-2 py-1 font-medium text-zinc-500 transition-colors hover:bg-zinc-100">Cancel</button>
                <button onClick={handleReset} className="rounded-md bg-zinc-900 px-2.5 py-1 font-medium text-white transition-colors hover:bg-zinc-800">Clear {scoredOnly.length}</button>
              </span>
            ) : (
              <button onClick={() => setConfirmReset(true)} title="Clear all scores back to the queue, then re-run selectively"
                className="inline-flex items-center gap-1.5 rounded-lg border border-zinc-200 bg-white px-3 py-2 text-[13px] font-medium text-zinc-500 transition-colors hover:bg-zinc-50 hover:text-zinc-700">
                <Icons.refresh className="h-4 w-4" />Reset
              </button>
            ))}
            <button onClick={() => setImporting(true)}
              className="inline-flex items-center gap-2 rounded-lg bg-indigo-600 px-3.5 py-2 text-[13px] font-medium text-white shadow-sm transition-colors hover:bg-indigo-700">
              <Icons.upload className="h-4 w-4" />Import accounts
            </button>
          </div>
        </div>

        {stats && (stats.scored_count > 0 || queuedCount > 0 || stats.total_cost > 0) && (
          <CostMeter stats={stats} queuedCount={queuedCount}
            onScoreBatch={handleScoreAll} batchRunning={batchRunning} />
        )}

        <div className="mt-6 overflow-hidden rounded-2xl border border-zinc-200 bg-white shadow-sm shadow-zinc-900/[0.02]">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-zinc-100 px-6 py-3.5">
            <div className="flex flex-wrap items-center gap-4">
              <Dropdown label="Segment" value={segF} onChange={setSegF}
                options={[{ value: 'all', label: 'All' }, { value: 'health_system', label: 'Health System' }, { value: 'specialty', label: 'Specialty' }, { value: 'payer', label: 'Payer' }]} />
              <Dropdown label="Fit" value={fitF} onChange={setFitF}
                options={[{ value: 'all', label: 'All' }, { value: 'high', label: 'High' }, { value: 'medium', label: 'Medium' }, { value: 'low', label: 'Low' }, { value: 'out', label: 'Not a fit' }]} />
              <Dropdown label="Source" value={sourceF} onChange={setSourceF}
                options={[{ value: 'all', label: 'All' }, { value: 'discovery', label: 'Discovery' }, { value: 'csv', label: 'CSV import' }]} />
              <Dropdown label="Date" value={dateF} onChange={setDateF}
                options={[{ value: 'all', label: 'All time' }, { value: 'today', label: 'Today' }, { value: '7d', label: 'Last 7 days' }, { value: '30d', label: 'Last 30 days' }]} />
              {imports.length > 0 && (
                <Dropdown label="Import" value={importF} onChange={setImportF}
                  options={[{ value: 'all', label: 'All imports' },
                    ...imports.map((im) => ({ value: im.label, label: `${im.label} (${im.count})` }))]} />
              )}
            </div>
            {scoredOnly.length > 0
              ? <FitLegend counts={fitCounts} />
              : <span className="text-[13px] text-zinc-400">{visible} {visible === 1 ? 'account' : 'accounts'}</span>}
          </div>

          {selected.size > 0 && (
            <div className="no-print flex flex-wrap items-center gap-3 border-b border-zinc-100 bg-indigo-50/40 px-6 py-2 text-[12.5px]">
              <span className="font-medium text-indigo-700">{selected.size} selected</span>
              {!allFilteredSelected && filteredScoredIds.length > selected.size && (
                <button onClick={toggleSelectAll} className="text-indigo-600 transition-colors hover:text-indigo-800">Select all {filteredScoredIds.length}</button>
              )}
              <button onClick={() => setSelected(new Set())} className="text-zinc-400 transition-colors hover:text-zinc-600">Clear</button>
              <span className="ml-auto text-zinc-400">Click Export to download {selected.size}</span>
            </div>
          )}

          {loading ? (
            <div className="animate-pulse">{Array.from({ length: 5 }).map((_, i) => <ScoredSkeletonRow key={i} />)}</div>
          ) : visible === 0 ? (
            <div className="flex flex-col items-center justify-center py-24 text-center">
              <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-zinc-100 text-zinc-400"><Icons.layers className="h-7 w-7" /></div>
              <h3 className="mt-5 text-[15px] font-semibold text-zinc-900">No scored accounts yet</h3>
              <p className="mt-1.5 max-w-xs text-[13px] text-zinc-500">Promote a company from Discovery, or import a CSV to start scoring.</p>
              <button onClick={() => setImporting(true)} className="mt-5 inline-flex items-center gap-2 rounded-lg bg-zinc-900 px-3.5 py-2 text-[13px] font-medium text-white transition-colors hover:bg-zinc-800">
                <Icons.upload className="h-4 w-4" />Import accounts
              </button>
            </div>
          ) : (
            scoredList.map((a) => (
              <ScoredRow key={a.account_id} account={a} batchRunning={batchRunning}
                selected={selected.has(a.account_id)} onToggleSelect={() => toggleSelect(a.account_id)}
                onOpen={() => setOpenAcc(a.account_id)} onScore={() => handleScore(a.account_id)}
                onLanding={() => setOpenLanding(a.account_id)} />
            ))
          )}
        </div>
        <p className="mt-4 text-center text-[12px] text-zinc-400">Promoted accounts and CSV imports converge here · QA runs independently on every score</p>
      </main>

      <ScoreDrawer account={openAccount} onClose={() => setOpenAcc(null)}
        onRescore={() => { if (openAccount) handleScore(openAccount.account_id); setOpenAcc(null); }}
        onOpenLanding={() => { if (openAccount) { setOpenLanding(openAccount.account_id); setOpenAcc(null); } }} />

      <LandingPageModal account={landingAccount} onClose={() => setOpenLanding(null)} pushToast={pushToast} />

      {importing && <ImportModal onClose={() => setImporting(false)} onImported={handleImported} pushToast={pushToast} />}
    </>
  );
}

ReactDOM.createRoot(document.getElementById('root')).render(<App />);
