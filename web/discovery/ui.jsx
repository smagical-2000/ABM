// ── Helpers ───────────────────────────────────────────────────────────────
function relativeTime(iso) {
  const then = new Date(iso).getTime();
  const diff = Math.max(0, window.NOW - then);
  const min = Math.round(diff / 60000);
  if (min < 60) return min <= 1 ? 'just now' : `${min}m ago`;
  const hr = Math.round(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const d = Math.round(hr / 24);
  if (d < 30) return `${d}d ago`;
  return new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}
function shortDate(iso) {
  return new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}
window.relativeTime = relativeTime;
window.shortDate = shortDate;

// ── Icons (simple stroke SVGs) ──────────────────────────────────────────────
const ic = (paths, props = {}) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6"
    strokeLinecap="round" strokeLinejoin="round" {...props}>{paths}</svg>
);
const Icons = {
  layoff: (p) => ic(<><path d="M16 17l5-5-5-5" /><path d="M21 12H9" /><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" /></>, p),
  leadership: (p) => ic(<><circle cx="12" cy="8" r="3.2" /><path d="M5.5 20a6.5 6.5 0 0 1 13 0" /></>, p),
  acquisition: (p) => ic(<><path d="M3 21V8l6-4 6 4M15 21V11l6 4v6" /><path d="M3 21h18" /><path d="M7 12h.01M7 16h.01" /></>, p),
  arrowUp: (p) => ic(<><path d="M12 19V5" /><path d="M6 11l6-6 6 6" /></>, p),
  moon: (p) => ic(<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z" />, p),
  x: (p) => ic(<><path d="M18 6 6 18M6 6l12 12" /></>, p),
  ext: (p) => ic(<><path d="M7 17 17 7" /><path d="M9 7h8v8" /></>, p),
  arrowRight: (p) => ic(<path d="M5 12h14M13 6l6 6-6 6" />, p),
  refresh: (p) => ic(<><path d="M21 12a9 9 0 1 1-2.64-6.36" /><path d="M21 4v5h-5" /></>, p),
  chevron: (p) => ic(<path d="m6 9 6 6 6-6" />, p),
  search: (p) => ic(<><circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" /></>, p),
  check: (p) => ic(<path d="M20 6 9 17l-5-5" />, p),
  sparkle: (p) => ic(<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z" />, p),
  inbox: (p) => ic(<><path d="M22 12h-6l-2 3h-4l-2-3H2" /><path d="M5.5 6h13l3.5 6v6a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2v-6z" /></>, p),
  clock: (p) => ic(<><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3.5 2" /></>, p),
  zap: (p) => ic(<path d="M13 2 4 14h7l-1 8 9-12h-7z" />, p),
  info: (p) => ic(<><circle cx="12" cy="12" r="9" /><path d="M12 11v5M12 8h.01" /></>, p),
};
window.Icons = Icons;

const SIGNAL_META = {
  layoff: { label: 'Layoff', icon: Icons.layoff, chip: 'bg-rose-50 text-rose-700 ring-rose-100', dot: 'bg-rose-400' },
  leadership_change: { label: 'Leadership', icon: Icons.leadership, chip: 'bg-sky-50 text-sky-700 ring-sky-100', dot: 'bg-sky-400' },
  acquisition: { label: 'Acquisition', icon: Icons.acquisition, chip: 'bg-amber-50 text-amber-700 ring-amber-100', dot: 'bg-amber-400' },
};
window.SIGNAL_META = SIGNAL_META;

const SEGMENT_META = {
  health_system: { label: 'Health System', cls: 'bg-blue-50 text-blue-700 ring-blue-100', dot: 'bg-blue-400' },
  specialty: { label: 'Specialty', cls: 'bg-teal-50 text-teal-700 ring-teal-100', dot: 'bg-teal-400' },
  payer: { label: 'Payer', cls: 'bg-violet-50 text-violet-700 ring-violet-100', dot: 'bg-violet-400' },
};
window.SEGMENT_META = SEGMENT_META;

// ── SegmentBadge ────────────────────────────────────────────────────────────
function SegmentBadge({ segment, size = 'sm' }) {
  const m = SEGMENT_META[segment];
  const pad = size === 'lg' ? 'px-2.5 py-1 text-[13px]' : 'px-2 py-0.5 text-[11px]';
  if (!m) {
    return (
      <span className={`inline-flex items-center gap-1.5 rounded-full ${pad} font-medium ring-1 ring-inset bg-zinc-100 text-zinc-500 ring-zinc-200`}>
        <span className="h-1.5 w-1.5 rounded-full bg-zinc-300" />Unclassified
      </span>
    );
  }
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full ${pad} font-medium ring-1 ring-inset ${m.cls}`}>
      <span className={`h-1.5 w-1.5 rounded-full ${m.dot}`} />{m.label}
    </span>
  );
}
window.SegmentBadge = SegmentBadge;

// ── ConfidenceMeter ─────────────────────────────────────────────────────────
function confColor(c) {
  if (c >= 0.85) return { bar: 'bg-emerald-500', text: 'text-emerald-600' };
  if (c >= 0.7) return { bar: 'bg-amber-500', text: 'text-amber-600' };
  return { bar: 'bg-orange-500', text: 'text-orange-600' };
}
function ConfidenceMeter({ value, size = 'sm' }) {
  const { bar, text } = confColor(value);
  const pct = Math.round(value * 100);
  if (size === 'lg') {
    return (
      <div className="flex items-center gap-3">
        <div className="h-2 flex-1 rounded-full bg-zinc-100 overflow-hidden">
          <div className={`h-full rounded-full ${bar}`} style={{ width: `${pct}%` }} />
        </div>
        <span className={`tabular-nums text-sm font-semibold ${text}`}>{pct}%</span>
      </div>
    );
  }
  return (
    <div className="flex items-center gap-2 w-[112px]">
      <div className="h-1.5 flex-1 rounded-full bg-zinc-100 overflow-hidden">
        <div className={`h-full rounded-full ${bar}`} style={{ width: `${pct}%` }} />
      </div>
      <span className={`tabular-nums text-xs font-semibold ${text} w-8 text-right`}>{pct}%</span>
    </div>
  );
}
window.ConfidenceMeter = ConfidenceMeter;

// ── SignalChip ──────────────────────────────────────────────────────────────
function SignalChip({ signal }) {
  const m = SIGNAL_META[signal.signal_type] || { chip: 'bg-zinc-50 text-zinc-600 ring-zinc-100', icon: Icons.sparkle };
  const Icon = m.icon;
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-[12px] font-medium ring-1 ring-inset ${m.chip}`}>
      <Icon className="h-3.5 w-3.5 shrink-0 opacity-80" />
      <span className="truncate max-w-[220px]">{signal.summary}</span>
    </span>
  );
}
window.SignalChip = SignalChip;

// ── StatTile ────────────────────────────────────────────────────────────────
function StatTile({ value, label, emphasized }) {
  return (
    <div className={`rounded-xl border px-5 py-4 transition-colors ${emphasized
      ? 'border-indigo-200 bg-indigo-50/60'
      : 'border-zinc-200 bg-white'}`}>
      <div className={`text-[28px] leading-none font-semibold tabular-nums ${emphasized ? 'text-indigo-700' : 'text-zinc-900'}`}>{value}</div>
      <div className={`mt-2 text-[13px] ${emphasized ? 'text-indigo-600/80' : 'text-zinc-500'}`}>{label}</div>
    </div>
  );
}
window.StatTile = StatTile;

// ── EmptyState ──────────────────────────────────────────────────────────────
function EmptyState({ onRun, variant }) {
  const needs = variant === 'needs_review';
  return (
    <div className="flex flex-col items-center justify-center py-24 text-center">
      <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-zinc-100 text-zinc-400">
        <Icons.inbox className="h-7 w-7" />
      </div>
      <h3 className="mt-5 text-[15px] font-semibold text-zinc-900">{needs ? 'Nothing to review' : "You're all caught up"}</h3>
      <p className="mt-1.5 max-w-xs text-[13px] text-zinc-500">
        {needs
          ? 'No ambiguous companies right now. Anything the AI can’t confidently classify will land here for your call.'
          : 'Nothing in the queue. Discovery runs on a schedule and will surface new qualified companies as they appear.'}
      </p>
      {!needs && (
        <button onClick={onRun}
          className="mt-5 inline-flex items-center gap-2 rounded-lg bg-zinc-900 px-3.5 py-2 text-[13px] font-medium text-white transition-colors hover:bg-zinc-800">
          <Icons.refresh className="h-4 w-4" />Run discovery
        </button>
      )}
    </div>
  );
}
window.EmptyState = EmptyState;

// ── Toasts ──────────────────────────────────────────────────────────────────
function ToastStack({ toasts }) {
  return (
    <div className="pointer-events-none fixed bottom-6 left-1/2 z-50 flex -translate-x-1/2 flex-col items-center gap-2">
      {toasts.map((t) => {
        const tone = t.tone === 'danger' ? 'text-rose-300' : t.tone === 'muted' ? 'text-zinc-400' : 'text-emerald-300';
        return (
          <div key={t.id}
            className="pointer-events-auto flex items-center gap-2.5 rounded-xl bg-zinc-900 px-4 py-2.5 text-[13px] font-medium text-white shadow-lg shadow-zinc-900/20 animate-toast">
            <Icons.check className={`h-4 w-4 ${tone}`} />
            {t.message}
          </div>
        );
      })}
    </div>
  );
}
window.ToastStack = ToastStack;
