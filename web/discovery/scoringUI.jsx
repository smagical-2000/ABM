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
  download: (p) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" {...p}>
      <path d="M12 3v12m0 0l-4-4m4 4l4-4M5 21h14" />
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

// One fit vocabulary across every segment — the ring color carries it, so this
// is just the word + legend tone. Tier 1-4 language is kept only inside the
// Health System detail (and any board export), never on the dashboard.
const FIT_META = {
  high: { word: 'High', dot: 'bg-emerald-500', tone: 'text-emerald-600' },
  medium: { word: 'Medium', dot: 'bg-amber-500', tone: 'text-amber-600' },
  low: { word: 'Low', dot: 'bg-zinc-400', tone: 'text-zinc-500' },
  out: { word: 'Not a fit', dot: 'bg-rose-500', tone: 'text-rose-600' },
};
window.FIT_META = FIT_META;
window.fitWord = (band) => (FIT_META[band] || FIT_META.low).word;

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
  verified: { label: 'Independently verified', icon: 'shieldCheck', cls: 'bg-emerald-50 text-emerald-700 ring-emerald-100', iccls: 'text-emerald-500' },
  discrepancy: { label: 'QA discrepancy', icon: 'alert', cls: 'bg-amber-50 text-amber-700 ring-amber-100', iccls: 'text-amber-500' },
  unverifiable: { label: 'Could not verify', icon: 'help', cls: 'bg-zinc-100 text-zinc-500 ring-zinc-200', iccls: 'text-zinc-400' },
  skipped: { label: 'QA not run', icon: 'help', cls: 'bg-zinc-100 text-zinc-500 ring-zinc-200', iccls: 'text-zinc-400' },
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

// ── DiscoverySignals — WHY this account was discovered, with proof links ─────
// A promoted lead must not "lose" the signal that surfaced it. This renders
// EVERY carried discovery signal (hiring, layoff, leadership, social engagement,
// event…) as its segment-colored chip + the summary + a "proof" link to the
// source (job posting, post, article). Renders nothing for CSV imports (no
// discovery signals), so it's safe to drop into the drawer unconditionally.
function DiscoverySignals({ signals }) {
  const list = (signals || []).filter((s) => s && s.signal_type);
  if (!list.length) return null;
  return (
    <div className="mt-3 rounded-lg bg-zinc-50 px-3 py-2.5 ring-1 ring-inset ring-zinc-200">
      <div className="mb-1.5 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide text-zinc-500">
        <window.Icons.compass className="h-3.5 w-3.5 text-zinc-400" />
        Why discovered · {list.length} {list.length === 1 ? 'signal' : 'signals'}
      </div>
      <ul className="space-y-1.5">
        {list.map((s, i) => {
          const m = window.SIGNAL_META[s.signal_type] || {};
          const Icon = m.icon || window.Icons.sparkle;
          return (
            <li key={i} className="flex items-start gap-2 text-[12.5px] text-zinc-700">
              <span className={`mt-0.5 inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide ring-1 ring-inset ${m.chip || 'bg-zinc-100 text-zinc-500 ring-zinc-200'}`}>
                <Icon className="h-3 w-3" />{m.label || s.signal_type}
              </span>
              <span className="min-w-0 flex-1 text-pretty">
                {s.summary || s.signal_type}
                {s.url && (
                  <a href={s.url} target="_blank" rel="noreferrer"
                    className="ml-1 inline-flex items-center gap-0.5 whitespace-nowrap text-indigo-600 hover:underline">
                    proof<window.Icons.ext className="h-3 w-3" />
                  </a>
                )}
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
window.DiscoverySignals = DiscoverySignals;

// ── DimensionBar (mirrors ConfidenceMeter) ───────────────────────────────────
function dimColor(ratio) {
  if (ratio >= 0.8) return { bar: 'bg-emerald-500', text: 'text-emerald-600' };
  if (ratio >= 0.5) return { bar: 'bg-amber-500', text: 'text-amber-600' };
  return { bar: 'bg-rose-400', text: 'text-rose-500' };
}
const FLAG_LABELS = { inferred: 'Estimated', unknown: 'Unconfirmed' };
function FlagChip({ flag }) {
  const estimated = flag === 'inferred';
  const label = FLAG_LABELS[flag] || flag;
  const title = flag === 'inferred'
    ? 'Estimated from patterns — not a confirmed CSV or web fact'
    : flag === 'unknown'
      ? 'Could not confirm — score is conservative'
      : flag;
  return (
    <span title={title}
      className={`inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[10.5px] font-medium uppercase tracking-wide ring-1 ring-inset ${estimated ? 'bg-amber-50 text-amber-600 ring-amber-100' : 'bg-zinc-100 text-zinc-400 ring-zinc-200'}`}>
      {label}
    </span>
  );
}
window.FlagChip = FlagChip;

function DimensionRow({ dim, correction, analystScore }) {
  const ratio = dim.score / dim.max;
  const { bar, text } = dimColor(ratio);
  const adjusted = analystScore != null && analystScore !== dim.score;
  return (
    <div className="py-3.5 border-b border-zinc-100 last:border-0">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2 min-w-0">
          <span className="truncate text-[13.5px] font-medium text-zinc-800">{dim.label}</span>
          {(dim.flags || []).map((f, i) => <FlagChip key={i} flag={f} />)}
        </div>
        <span className={`shrink-0 tabular-nums text-[13px] font-semibold ${text}`}>
          {adjusted && <span className="mr-1 font-normal text-zinc-300 line-through">{analystScore}</span>}
          {dim.score}<span className="text-zinc-300 font-normal">/{dim.max}</span>
        </span>
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

// ── ScoringProgress ──────────────────────────────────────────────────────────
// Live progress for an in-flight score: the phase, elapsed time, and an
// estimate bar. An LLM call's duration varies with how many web searches it
// runs, so this is an honest estimate (elapsed vs a typical run), not a fake
// exact percentage. Two phases: researching & scoring, then independent QA.
function ScoringProgress({ account }) {
  const EST = 50;     // typical seconds for score + QA
  const STALL = 180;  // past ~3x typical, stop pretending it is on schedule
  const start = account.scoring_started_at ? new Date(account.scoring_started_at).getTime() : null;
  const [elapsed, setElapsed] = React.useState(account.elapsed_seconds || 0);
  React.useEffect(() => {
    if (!start) return undefined;
    const tick = () => setElapsed(Math.max(0, Math.round((Date.now() - start) / 1000)));
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, [start]);
  const stalled = elapsed > STALL;
  const pct = stalled ? 100 : Math.round(Math.min(0.96, elapsed / EST) * 100);
  const verifying = account.phase === 'verifying';
  const mmss = (s) => { const m = Math.floor(s / 60); return m ? `${m}m ${s % 60}s` : `${s}s`; };
  return (
    <div className="hidden w-[210px] shrink-0 lg:block">
      <div className="flex items-center justify-between text-[11px]">
        <span className={`font-medium ${stalled ? 'text-amber-600' : 'text-indigo-600'}`}>
          {stalled ? 'Taking longer than usual' : (verifying ? 'Verifying · independent QA' : 'Researching & scoring')}
        </span>
        <span className="tabular-nums text-zinc-400">
          {stalled ? mmss(elapsed) : <>{elapsed}s<span className="text-zinc-300"> / ~{EST}s</span></>}
        </span>
      </div>
      <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-zinc-100">
        <div className={`h-full rounded-full ${stalled ? 'animate-pulse bg-amber-400' : 'bg-indigo-400'}`}
          style={{ width: `${pct}%`, transition: 'width 1s linear' }} />
      </div>
    </div>
  );
}
window.ScoringProgress = ScoringProgress;

// ── ScoredRow ────────────────────────────────────────────────────────────────
function ScoredRow({ account, entering, onOpen, onScore, onLanding, tw, batchRunning, selected, onToggleSelect }) {
  const a = account;
  const compact = (tw || {}).density === 'compact';
  const tier = a.tier || (a.total != null ? window.tierFor(a.framework, a.total) : null);
  const stop = (fn) => (e) => { e.stopPropagation(); fn && fn(); };
  const clickable = a.state === 'scored';
  // The only QA signal loud enough for the at-a-glance row: an independent
  // disagreement that moves the fit tier. Everything else lives in the drawer.
  const flagged = clickable && a.qa && a.qa.tier_changing;
  return (
    <div
      onClick={clickable ? onOpen : undefined}
      className={`group relative border-b border-zinc-100 px-6 ${compact ? 'py-3' : 'py-4'} transition-all duration-500
        ${clickable ? 'cursor-pointer hover:bg-zinc-50/70' : ''}
        ${entering ? 'opacity-0 translate-y-1' : 'opacity-100 translate-y-0'}`}>
      <div className="flex items-center gap-4">
        {/* selection checkbox (scored rows only); a spacer keeps others aligned */}
        {a.state === 'scored' && onToggleSelect ? (
          <input type="checkbox" checked={!!selected} onChange={stop(onToggleSelect)} onClick={(e) => e.stopPropagation()}
            aria-label={`Select ${a.name}`}
            className="no-print h-4 w-4 shrink-0 cursor-pointer rounded border-zinc-300 text-indigo-600 focus:ring-indigo-300" />
        ) : (
          <span className="no-print h-4 w-4 shrink-0" />
        )}
        {/* Left: identity only — the score breakdown opens in the drawer */}
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2.5">
            <h3 className="truncate text-[15px] font-semibold text-zinc-900">{a.name}</h3>
            <SegmentBadge segment={a.segment} />
            <AbmBadge match={a.abm_match} />
            {a.warm_intros && a.warm_intros.warm_count > 0 && (
              <span title={`${a.warm_intros.warm_count} warm intro path${a.warm_intros.warm_count === 1 ? '' : 's'} to a decision-maker — open to see how`}
                className="inline-flex shrink-0 items-center gap-1 rounded-full bg-amber-50 px-2 py-0.5 text-[10.5px] font-semibold text-amber-700 ring-1 ring-inset ring-amber-100">
                <Icons.leadership className="h-3 w-3" />{a.warm_intros.warm_count} warm
              </span>
            )}
            {flagged && (
              <span title="Independent QA disagrees on a fact that moves the fit. Open to review."
                className="h-1.5 w-1.5 shrink-0 rounded-full bg-amber-400" />
            )}
          </div>
          <div className="mt-1.5 flex items-center gap-1.5 text-[12px] text-zinc-400">
            <SourceTag source={a.source} />
            {a.state === 'scored' && (<><span className="text-zinc-300">·</span><span title={a.scored_at || ''}>scored {formatDateTime(a.scored_at)}</span></>)}
            {a.state === 'queued' && (<><span className="text-zinc-300">·</span><span>not scored</span></>)}
            {a.state === 'error' && (<><span className="text-zinc-300">·</span><span>score failed</span></>)}
          </div>
        </div>

        {/* Right: the colored ring is the fit (green High, amber Medium, grey
            Low, red Not a fit); other states get their own affordance */}
        <div className="flex shrink-0 items-center gap-3">
          {a.state === 'scoring' && <ScoringProgress account={a} />}
          {a.state === 'scored' && <ScoreRing total={a.total} max={a.max_total} band={tier.band} />}
          {a.state === 'queued' && (
            batchRunning ? (
              <span className="inline-flex items-center gap-1.5 rounded-lg bg-zinc-100 px-3 py-2 text-[12px] font-medium text-zinc-400">
                <Icons.refresh className="h-3.5 w-3.5 animate-spin" />In queue
              </span>
            ) : (
              <button onClick={stop(onScore)}
                className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-3.5 py-2 text-[12px] font-medium text-white shadow-sm transition-colors hover:bg-indigo-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-300">
                <Icons.sparkle className="h-3.5 w-3.5" />Score now
              </button>
            )
          )}
          {a.state === 'error' && (
            <button onClick={stop(onScore)}
              className="inline-flex items-center gap-1.5 rounded-lg border border-zinc-200 bg-white px-3 py-1.5 text-[12px] font-medium text-zinc-500 transition-colors hover:bg-zinc-50 hover:text-zinc-700">
              <Icons.refresh className="h-3.5 w-3.5" />Retry
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

// ── CostMeter ─────────────────────────────────────────────────────────────────
// The live spend control. Month-to-date scoring cost against the budget, plus
// the on-demand batch action for parked (queued) accounts. Imports cost nothing
// until scored here, and the confirm step won't let an accidental "score all"
// blow past the remaining budget — it offers a budget-fit batch instead.
function CostMeter({ stats, queuedCount, onScoreBatch, batchRunning }) {
  const [confirm, setConfirm] = React.useState(false);
  if (!stats) return null;
  const fmt = (n) => '$' + (Number(n) || 0).toFixed(2);
  const budget = stats.monthly_budget || 200;
  const month = Number(stats.month_cost) || 0;
  const remaining = stats.budget_remaining != null ? stats.budget_remaining : Math.max(0, budget - month);
  const pct = Math.min(100, Math.round((month / budget) * 100));
  const bar = pct >= 90 ? 'bg-rose-500' : pct >= 70 ? 'bg-amber-500' : 'bg-emerald-500';
  const est = stats.avg_cost > 0 ? stats.avg_cost : 0.25;        // measured average once we have one
  const allCost = queuedCount * est;
  const overBudget = allCost > remaining + 0.001;
  const fit = Math.max(0, Math.floor(remaining / est));          // accounts that fit the remaining budget
  const fire = (limit) => { setConfirm(false); onScoreBatch(limit); };

  return (
    <div className="mt-4 rounded-2xl border border-zinc-200 bg-white px-5 py-4 shadow-sm shadow-zinc-900/[0.02]">
      <div className="flex flex-wrap items-center justify-between gap-x-8 gap-y-4">
        <div className="min-w-[240px] flex-1">
          <div className="flex items-baseline justify-between gap-3">
            <span className="text-[11px] font-medium uppercase tracking-wide text-zinc-400">Scoring spend · this month</span>
            <span className="text-[12px] tabular-nums text-zinc-500"><span className="font-semibold text-zinc-800">{fmt(month)}</span> / {fmt(budget)}</span>
          </div>
          <div className="mt-2 h-2 w-full overflow-hidden rounded-full bg-zinc-100">
            <div className={`h-full rounded-full ${bar} transition-all duration-500`} style={{ width: Math.max(2, pct) + '%' }} />
          </div>
          <div className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[11.5px] text-zinc-400">
            <span>{fmt(remaining)} left this month</span>
            <span className="text-zinc-300">·</span>
            <span>{fmt(stats.total_cost)} all-time</span>
            {stats.avg_cost > 0 && (<><span className="text-zinc-300">·</span><span>{fmt(stats.avg_cost)}/account</span></>)}
          </div>
        </div>

        {queuedCount > 0 && (
          <div className="flex shrink-0 items-center gap-3">
            {!confirm ? (
              <>
                <div className="text-right">
                  <div className="text-[13px] font-semibold text-zinc-800">{queuedCount.toLocaleString()} queued</div>
                  <div className="text-[11.5px] text-zinc-400">~{fmt(allCost)} to score all</div>
                </div>
                <button onClick={() => setConfirm(true)} disabled={batchRunning}
                  className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-3.5 py-2 text-[12.5px] font-medium text-white shadow-sm transition-colors hover:bg-indigo-700 disabled:cursor-not-allowed disabled:opacity-50">
                  {batchRunning
                    ? (<><Icons.refresh className="h-3.5 w-3.5 animate-spin" />Scoring…</>)
                    : (<><Icons.sparkle className="h-3.5 w-3.5" />Score queued</>)}
                </button>
              </>
            ) : (
              <div className="flex flex-col items-end gap-1.5">
                {overBudget && (
                  <span className="text-[11.5px] text-rose-600">Scoring all {queuedCount.toLocaleString()} (~{fmt(allCost)}) exceeds your {fmt(remaining)} remaining.</span>
                )}
                <div className="flex items-center gap-2">
                  <button onClick={() => setConfirm(false)}
                    className="rounded-lg px-2.5 py-1.5 text-[12px] font-medium text-zinc-500 transition-colors hover:bg-zinc-100">Cancel</button>
                  {overBudget && fit > 0 && (
                    <button onClick={() => fire(fit)}
                      className="rounded-lg bg-indigo-600 px-3 py-1.5 text-[12px] font-medium text-white transition-colors hover:bg-indigo-700">Score {fit} within budget</button>
                  )}
                  <button onClick={() => fire(null)}
                    className={`rounded-lg px-3 py-1.5 text-[12px] font-medium text-white transition-colors ${overBudget ? 'bg-rose-600 hover:bg-rose-700' : 'bg-indigo-600 hover:bg-indigo-700'}`}>
                    Score all {queuedCount.toLocaleString()} (~{fmt(allCost)})
                  </button>
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
window.CostMeter = CostMeter;
