// ── Scoring UI components ────────────────────────────────────────────────────
// Built on the Discovery design language: zinc neutrals, indigo primary,
// rounded-2xl white cards, calm segment/state colors.

// extra icons layered onto the Discovery set
Object.assign(window.Icons, {
  shieldCheck: (p) => window.Icons && (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" {...p}>
      <path d="M12 3l7 3v5c0 4.6-3 7.6-7 9-4-1.4-7-4.4-7-9V6z" /><path d="M9 12l2 2 4-4" />
    </svg>
  ),
  alert: (p) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" {...p}>
      <path d="M10.3 3.7 2.5 17a2 2 0 0 0 1.7 3h15.6a2 2 0 0 0 1.7-3L13.7 3.7a2 2 0 0 0-3.4 0z" /><path d="M12 9v4M12 17h.01" />
    </svg>
  ),
  help: (p) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" {...p}>
      <circle cx="12" cy="12" r="9" /><path d="M9.2 9.2a2.8 2.8 0 0 1 5.4 1c0 1.8-2.6 2.2-2.6 4M12 17h.01" />
    </svg>
  ),
  upload: (p) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" {...p}>
      <path d="M12 16V4M7 9l5-5 5 5" /><path d="M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2" />
    </svg>
  ),
  doc: (p) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" {...p}>
      <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" /><path d="M14 3v5h5" /><path d="M9 13h6M9 17h6" />
    </svg>
  ),
  compass: (p) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" {...p}>
      <circle cx="12" cy="12" r="9" /><path d="m15.5 8.5-2 5-5 2 2-5z" />
    </svg>
  ),
  layers: (p) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" {...p}>
      <path d="m12 3 9 5-9 5-9-5z" /><path d="m3 13 9 5 9-5" />
    </svg>
  ),
  plus: (p) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" {...p}>
      <path d="M12 5v14M5 12h14" />
    </svg>
  ),
});

// ── Tier band styling ────────────────────────────────────────────────────────
const BAND_STYLE = {
  high: { ring: '#10b981', track: '#d1fae5', pill: 'bg-emerald-50 text-emerald-700 ring-emerald-100', dot: 'bg-emerald-500', text: 'text-emerald-600', soft: 'text-emerald-700' },
  medium: { ring: '#f59e0b', track: '#fef3c7', pill: 'bg-amber-50 text-amber-700 ring-amber-100', dot: 'bg-amber-500', text: 'text-amber-600', soft: 'text-amber-700' },
  low: { ring: '#94a3b8', track: '#e5e7eb', pill: 'bg-zinc-100 text-zinc-600 ring-zinc-200', dot: 'bg-zinc-400', text: 'text-zinc-500', soft: 'text-zinc-600' },
  out: { ring: '#f43f5e', track: '#ffe4e6', pill: 'bg-rose-50 text-rose-700 ring-rose-100', dot: 'bg-rose-500', text: 'text-rose-600', soft: 'text-rose-700' },
};
window.BAND_STYLE = BAND_STYLE;

// ── ScoreRing ────────────────────────────────────────────────────────────────
function ScoreRing({ total, max, band, size = 'sm', loading = false }) {
  const dim = size === 'lg' ? 92 : size === 'md' ? 64 : 52;
  const stroke = size === 'lg' ? 7 : size === 'md' ? 5.5 : 4.5;
  const r = (dim - stroke) / 2;
  const circ = 2 * Math.PI * r;
  const st = BAND_STYLE[band] || BAND_STYLE.low;
  const ratio = loading || total == null ? 0 : Math.max(0, Math.min(1, total / max));
  const numCls = size === 'lg' ? 'text-[30px]' : size === 'md' ? 'text-[20px]' : 'text-[16px]';
  const maxCls = size === 'lg' ? 'text-[12px]' : 'text-[10px]';
  return (
    <div className="relative shrink-0" style={{ width: dim, height: dim }}>
      <svg width={dim} height={dim} className={loading ? 'animate-spin-slow' : ''} style={{ transform: 'rotate(-90deg)' }}>
        <circle cx={dim / 2} cy={dim / 2} r={r} fill="none" stroke={loading ? '#e4e4e7' : st.track} strokeWidth={stroke} />
        <circle
          cx={dim / 2} cy={dim / 2} r={r} fill="none"
          stroke={loading ? '#c7d2fe' : st.ring} strokeWidth={stroke} strokeLinecap="round"
          strokeDasharray={circ}
          strokeDashoffset={loading ? circ * 0.72 : circ * (1 - ratio)}
          style={{ transition: 'stroke-dashoffset 0.9s cubic-bezier(0.16,1,0.3,1), stroke 0.4s ease' }} />
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center leading-none">
        {loading ? (
          <span className={`${maxCls} font-medium text-zinc-400`}>···</span>
        ) : (
          <>
            <span className={`${numCls} font-semibold tabular-nums text-zinc-900`}>{total}</span>
            <span className={`${maxCls} font-medium tabular-nums text-zinc-400`}>/{max}</span>
          </>
        )}
      </div>
    </div>
  );
}
window.ScoreRing = ScoreRing;

// ── TierBadge ────────────────────────────────────────────────────────────────
function TierBadge({ band, label, size = 'sm' }) {
  const st = BAND_STYLE[band] || BAND_STYLE.low;
  const pad = size === 'lg' ? 'px-2.5 py-1 text-[13px]' : 'px-2 py-0.5 text-[11px]';
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full ${pad} font-medium ring-1 ring-inset ${st.pill}`}>
      <span className={`h-1.5 w-1.5 rounded-full ${st.dot}`} />{label}
    </span>
  );
}
window.TierBadge = TierBadge;

// ── QABadge ──────────────────────────────────────────────────────────────────
const QA_META = {
  verified: { label: 'QA verified', icon: 'shieldCheck', cls: 'bg-emerald-50 text-emerald-700 ring-emerald-100', iccls: 'text-emerald-500' },
  discrepancy: { label: 'QA discrepancy', icon: 'alert', cls: 'bg-amber-50 text-amber-700 ring-amber-100', iccls: 'text-amber-500' },
  unverifiable: { label: 'Unverifiable', icon: 'help', cls: 'bg-zinc-100 text-zinc-500 ring-zinc-200', iccls: 'text-zinc-400' },
};
window.QA_META = QA_META;

function QABadge({ status, tierChanging = false, size = 'sm' }) {
  const m = QA_META[status];
  if (!m) return null;
  const Icon = window.Icons[m.icon];
  const pad = size === 'lg' ? 'px-2.5 py-1 text-[12px]' : 'px-2 py-0.5 text-[11px]';
  const cls = tierChanging ? 'bg-rose-50 text-rose-700 ring-rose-100' : m.cls;
  const ic = tierChanging ? 'text-rose-500' : m.iccls;
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full ${pad} font-medium ring-1 ring-inset ${cls}`}>
      <Icon className={`h-3.5 w-3.5 ${ic}`} />
      {tierChanging ? 'Tier conflict' : m.label}
    </span>
  );
}
window.QABadge = QABadge;

// ── SourceTag ────────────────────────────────────────────────────────────────
function SourceTag({ source }) {
  const isCsv = source === 'csv';
  const Icon = isCsv ? window.Icons.doc : window.Icons.compass;
  return (
    <span className="inline-flex items-center gap-1 text-[12px] text-zinc-400">
      <Icon className="h-3.5 w-3.5" />{isCsv ? 'CSV import' : 'Discovery'}
    </span>
  );
}
window.SourceTag = SourceTag;

// ── DimensionBar (mirrors ConfidenceMeter) ───────────────────────────────────
function dimColor(ratio) {
  if (ratio >= 0.8) return { bar: 'bg-emerald-500', text: 'text-emerald-600' };
  if (ratio >= 0.5) return { bar: 'bg-amber-500', text: 'text-amber-600' };
  return { bar: 'bg-rose-400', text: 'text-rose-500' };
}
function FlagChip({ flag }) {
  const known = flag === 'inferred';
  return (
    <span className={`inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[10.5px] font-medium uppercase tracking-wide ring-1 ring-inset ${known ? 'bg-amber-50 text-amber-600 ring-amber-100' : 'bg-zinc-100 text-zinc-400 ring-zinc-200'}`}>
      {flag}
    </span>
  );
}
window.FlagChip = FlagChip;

function DimensionRow({ dim, correction }) {
  const ratio = dim.score / dim.max;
  const { bar, text } = dimColor(ratio);
  return (
    <div className="py-3.5 border-b border-zinc-100 last:border-0">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2 min-w-0">
          <span className="truncate text-[13.5px] font-medium text-zinc-800">{dim.label}</span>
          {(dim.flags || []).map((f, i) => <FlagChip key={i} flag={f} />)}
        </div>
        <span className={`shrink-0 tabular-nums text-[13px] font-semibold ${text}`}>{dim.score}<span className="text-zinc-300 font-normal">/{dim.max}</span></span>
      </div>
      <div className="mt-2 h-1.5 rounded-full bg-zinc-100 overflow-hidden">
        <div className={`h-full rounded-full ${bar}`} style={{ width: `${ratio * 100}%`, transition: 'width 0.7s cubic-bezier(0.16,1,0.3,1)' }} />
      </div>
      <p className="mt-2 text-[12.5px] leading-relaxed text-zinc-500 text-pretty">{dim.summary}</p>
      {correction && (
        <div className="mt-2.5 flex items-start gap-2 rounded-lg bg-amber-50/70 px-3 py-2 ring-1 ring-inset ring-amber-100">
          <window.Icons.alert className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-500" />
          <div className="text-[12px] leading-relaxed text-amber-800">
            <span className="font-medium">QA correction.</span> Scorer claimed <span className="font-medium">{correction.claimed}</span>; QA found <span className="font-medium">{correction.found}</span>.
          </div>
        </div>
      )}
    </div>
  );
}
window.DimensionRow = DimensionRow;

// ── Pillar rollup ────────────────────────────────────────────────────────────
// Collapses any framework into the three pillars the board reads at a glance:
// Firmographic · Technographic · Intent. Specialty/payer map 1:1; health-system
// rolls its six dimensions up into the three.
function pillarsFor(a) {
  const byKey = {}; (a.dimensions || []).forEach((d) => { byKey[d.key] = d; });
  const sum = (keys) => keys.reduce((o, k) => { const d = byKey[k]; if (d) { o.score += d.score; o.max += d.max; } return o; }, { score: 0, max: 0 });
  // The rollup (which dimensions feed Firmographic / Technographic / Business
  // Intent) comes from the framework config served by the API, so it never
  // drifts from the scorer. For health systems that means Technographic =
  // EMR + Tech Readiness, Business Intent = Competitors + Pain + Leadership.
  const fw = (window.FRAMEWORKS || {})[a.framework];
  if (fw && fw.pillars) {
    return fw.pillars.map((p) => ({ key: p.key, label: p.label, ...sum(p.dims) }));
  }
  // Fallback before the config loads — union of all known dimension keys.
  return [
    { key: 'firmographic', label: 'Firmographic', ...sum(['firmographic', 'npr']) },
    { key: 'technographic', label: 'Technographic', ...sum(['technographic', 'emr', 'ai_readiness']) },
    { key: 'intent', label: 'Business Intent', ...sum(['intent', 'competitor', 'pain', 'leadership']) },
  ];
}
window.pillarsFor = pillarsFor;

// compact three-line pillar readout for the list row
function PillarMeters({ account, band }) {
  const st = window.BAND_STYLE[band] || window.BAND_STYLE.low;
  return (
    <div className="hidden w-[184px] shrink-0 lg:block">
      {pillarsFor(account).map((p) => {
        const ratio = p.max ? p.score / p.max : 0;
        return (
          <div key={p.key} className="flex items-center gap-2.5 py-[3px]">
            <span className="w-[74px] shrink-0 text-[10.5px] tracking-tight text-zinc-400">{p.label}</span>
            <div className="h-1 flex-1 overflow-hidden rounded-full bg-zinc-100">
              <div className="h-full rounded-full" style={{ width: `${ratio * 100}%`, background: st.ring, opacity: 0.55 }} />
            </div>
            <span className="w-9 shrink-0 text-right text-[10.5px] font-medium tabular-nums text-zinc-500">{p.score}<span className="text-zinc-300">/{p.max}</span></span>
          </div>
        );
      })}
    </div>
  );
}
window.PillarMeters = PillarMeters;

// ── ScoredRow ────────────────────────────────────────────────────────────────
function ScoredRow({ account, entering, onOpen, onScore, onLanding, tw }) {
  const a = account;
  const t = tw || {};
  const compact = t.density === 'compact';
  const showQA = t.showQA !== false;
  const numberDisplay = t.scoreDisplay === 'number';
  const tier = a.tier || (a.total != null ? window.tierFor(a.framework, a.total) : null);
  const stop = (fn) => (e) => { e.stopPropagation(); fn && fn(); };
  const clickable = a.state === 'scored';
  return (
    <div
      onClick={clickable ? onOpen : undefined}
      className={`group relative border-b border-zinc-100 px-6 ${compact ? 'py-2.5' : 'py-4'} transition-all duration-500
        ${clickable ? 'cursor-pointer hover:bg-zinc-50/70' : ''}
        ${entering ? 'opacity-0 translate-y-1' : 'opacity-100 translate-y-0'}`}>
      <div className="flex items-center gap-4">
        {/* Left: identity */}
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2.5">
            <h3 className="truncate text-[15px] font-semibold text-zinc-900">{a.name}</h3>
            <SegmentBadge segment={a.segment} />
          </div>
          <div className={`${compact ? 'mt-1' : 'mt-2'} flex flex-wrap items-center gap-2`}>
            {a.state === 'scored' && tier && <TierBadge band={tier.band} label={tier.label} />}
            {a.state === 'scored' && showQA && a.qa && <QABadge status={a.qa.status} tierChanging={a.qa.tier_changing} />}
            {a.state === 'scoring' && (
              <span className="inline-flex items-center gap-1.5 rounded-full bg-indigo-50 px-2 py-0.5 text-[11px] font-medium text-indigo-600 ring-1 ring-inset ring-indigo-100">
                <span className="relative flex h-1.5 w-1.5"><span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-indigo-400 opacity-70" /><span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-indigo-500" /></span>
                Scoring…
              </span>
            )}
            {a.state === 'queued' && (
              <span className="inline-flex items-center gap-1.5 rounded-full bg-zinc-100 px-2 py-0.5 text-[11px] font-medium text-zinc-500 ring-1 ring-inset ring-zinc-200">
                Not scored yet
              </span>
            )}
          </div>
          <div className={`${compact ? 'mt-1' : 'mt-2'} flex items-center gap-1.5 text-[12px] text-zinc-400`}>
            <SourceTag source={a.source} />
            <span className="text-zinc-300">·</span>
            {a.state === 'scored'
              ? <span>scored {relativeTime(a.scored_at)}</span>
              : a.state === 'scoring'
                ? <span>started just now</span>
                : <span>awaiting score</span>}
            {a.approximate_employees && (<><span className="text-zinc-300">·</span><span>~{a.approximate_employees.toLocaleString()} staff</span></>)}
          </div>
        </div>

        {/* Middle: pillar scores */}
        {a.state === 'scored' && <PillarMeters account={a} band={tier.band} />}

        {/* Right: ring/number + action */}
        <div className="flex shrink-0 items-center gap-3">
          {a.state === 'scored' && (
            <button onClick={stop(onLanding)} title="Open Landing Page"
              className="hidden items-center gap-1.5 rounded-lg border border-zinc-200 bg-white px-2.5 py-1.5 text-[12px] font-medium text-zinc-500 opacity-0 transition-all hover:border-indigo-200 hover:bg-indigo-50/40 hover:text-indigo-700 group-hover:opacity-100 md:inline-flex">
              <Icons.doc className="h-3.5 w-3.5" />Landing Page
            </button>
          )}
          {a.state === 'scored' && (numberDisplay
            ? (<div className="text-right"><div className="text-[22px] font-semibold leading-none tabular-nums text-zinc-900">{a.total}<span className="text-[13px] font-normal text-zinc-300">/{a.max_total}</span></div></div>)
            : <ScoreRing total={a.total} max={a.max_total} band={tier.band} />)}
          {a.state === 'scoring' && <ScoreRing loading max={a.max_total} band="low" />}
          {a.state === 'queued' && (
            <button onClick={stop(onScore)}
              className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-3.5 py-2 text-[12px] font-medium text-white shadow-sm transition-colors hover:bg-indigo-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-300">
              <Icons.sparkle className="h-3.5 w-3.5" />Score now
            </button>
          )}
          {clickable && (
            <span className="text-zinc-300 transition-colors group-hover:text-zinc-500"><Icons.arrowRight className="h-4 w-4" /></span>
          )}
        </div>
      </div>
    </div>
  );
}
window.ScoredRow = ScoredRow;

// ── ScoredSkeletonRow ────────────────────────────────────────────────────────
function ScoredSkeletonRow() {
  return (
    <div className="border-b border-zinc-100 px-6 py-4">
      <div className="flex items-center gap-4">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2.5"><div className="h-4 w-52 rounded bg-zinc-100" /><div className="h-4 w-24 rounded-full bg-zinc-100" /></div>
          <div className="mt-3 flex gap-2"><div className="h-5 w-20 rounded-full bg-zinc-100" /><div className="h-5 w-24 rounded-full bg-zinc-100" /></div>
          <div className="mt-3 h-3 w-40 rounded bg-zinc-100" />
        </div>
        <div className="h-[52px] w-[52px] rounded-full bg-zinc-100" />
      </div>
    </div>
  );
}
window.ScoredSkeletonRow = ScoredSkeletonRow;
