const { useState } = React;

// ── Action buttons (shared between row and drawer) ──────────────────────────
function PromoteButton({ onClick, size = 'sm' }) {
  const pad = size === 'lg' ? 'px-4 py-2.5 text-[13px]' : 'px-3 py-1.5 text-[12px]';
  return (
    <button onClick={onClick}
      className={`inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 ${pad} font-medium text-white shadow-sm transition-colors hover:bg-indigo-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-300`}>
      <Icons.arrowUp className="h-3.5 w-3.5" />Score
    </button>
  );
}
function DeferButton({ onClick, size = 'sm' }) {
  const pad = size === 'lg' ? 'px-4 py-2.5 text-[13px]' : 'px-3 py-1.5 text-[12px]';
  return (
    <button onClick={onClick}
      className={`inline-flex items-center gap-1.5 rounded-lg border border-zinc-200 bg-white ${pad} font-medium text-zinc-600 transition-colors hover:bg-zinc-50 hover:text-zinc-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-zinc-200`}>
      <Icons.moon className="h-3.5 w-3.5" />Defer
    </button>
  );
}
function RejectButton({ onClick, size = 'sm' }) {
  const pad = size === 'lg' ? 'px-4 py-2.5 text-[13px]' : 'px-3 py-1.5 text-[12px]';
  return (
    <button onClick={onClick}
      className={`inline-flex items-center gap-1.5 rounded-lg ${pad} font-medium text-zinc-400 transition-colors hover:bg-rose-50 hover:text-rose-600 focus:outline-none focus-visible:ring-2 focus-visible:ring-rose-200`}>
      Reject
    </button>
  );
}
function RestoreButton({ onClick, size = 'sm' }) {
  const pad = size === 'lg' ? 'px-4 py-2.5 text-[13px]' : 'px-3 py-1.5 text-[12px]';
  return (
    <button onClick={onClick}
      className={`inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 ${pad} font-medium text-white shadow-sm transition-colors hover:bg-indigo-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-300`}>
      <Icons.refresh className="h-3.5 w-3.5" />Restore
    </button>
  );
}
window.PromoteButton = PromoteButton;
window.DeferButton = DeferButton;
window.RejectButton = RejectButton;
window.RestoreButton = RestoreButton;

// ── AbmBadge: this company is on the uploaded ABM target list ────────────────
function AbmBadge({ match }) {
  if (!match) return null;
  const confirmed = match.tier === 'confirmed';
  const where = [match.source_sheet, match.state].filter(Boolean).join(', ');
  const title = `On ABM target list: ${match.target_name}${where ? ` (${where})` : ''}`
    + (confirmed ? '' : ' — name-only match, verify location');
  return (
    <span title={title}
      className={`inline-flex shrink-0 items-center gap-1 rounded-md px-1.5 py-0.5 text-[10.5px] font-semibold uppercase tracking-wide ring-1 ring-inset
        ${confirmed ? 'bg-amber-100 text-amber-700 ring-amber-200' : 'bg-amber-50 text-amber-600/70 ring-amber-100'}`}>
      <Icons.sparkle className="h-3 w-3" />
      {confirmed ? 'ABM target' : 'ABM?'}
    </span>
  );
}
window.AbmBadge = AbmBadge;

// ── AbmCallout: the detail-drawer banner for an ABM-target match ─────────────
// Shared by the discovery drawer and the score drawer so a match reads
// identically on both. Renders nothing when the company isn't on the list.
function AbmCallout({ match }) {
  if (!match) return null;
  const confirmed = match.tier === 'confirmed';
  const where = [match.source_sheet, match.state].filter(Boolean).join(', ');
  return (
    <div className={`mt-3 flex items-start gap-2 rounded-lg px-3 py-2 text-[12.5px] ring-1 ring-inset
      ${confirmed ? 'bg-amber-50 text-amber-800 ring-amber-200' : 'bg-amber-50/60 text-amber-700 ring-amber-100'}`}>
      <Icons.sparkle className="mt-0.5 h-4 w-4 shrink-0 text-amber-500" />
      <div>
        <span className="font-semibold">
          {confirmed ? 'On your ABM target list' : 'Possible ABM-list match'}
        </span>
        {' — '}{match.target_name}{where ? ` · ${where}` : ''}
        {!confirmed && (
          <span className="mt-0.5 block text-amber-600/80">Name match only — verify it's the same organization.</span>
        )}
      </div>
    </div>
  );
}
window.AbmCallout = AbmCallout;

// ── CompanyRow ──────────────────────────────────────────────────────────────
// The buying-intent readout on a row: a fill bar + the score + the Hot/Watch pill.
function IntentMeter({ tier, score }) {
  if (!tier) return null;
  const hot = tier === 'hot';
  return (
    <div className="flex shrink-0 items-center gap-3" title={`Buying intent ${score} of 100`}>
      <div className="hidden w-20 sm:block">
        <div className="h-1.5 rounded-full bg-zinc-100">
          <div className={`h-full rounded-full ${hot ? 'bg-amber-500' : 'bg-zinc-300'}`}
            style={{ width: `${Math.max(4, Math.min(100, score))}%` }} />
        </div>
      </div>
      <div className={`w-7 text-right text-[18px] font-semibold tabular-nums ${hot ? 'text-zinc-900' : 'text-zinc-400'}`}>{score}</div>
      <span className={`inline-flex w-[60px] shrink-0 items-center justify-center gap-1 rounded-full px-2 py-1 text-[11px] font-semibold ring-1 ring-inset ${hot ? 'bg-amber-50 text-amber-700 ring-amber-100' : 'bg-zinc-100 text-zinc-500 ring-zinc-200'}`}>
        {hot ? <><Icons.zap className="h-3 w-3" />Hot</> : <><Icons.clock className="h-3 w-3" />Watch</>}
      </span>
    </div>
  );
}
window.IntentMeter = IntentMeter;

// The divider between the Hot leads (auto-scored) and the Watch leads (held).
function AutoScoreLine() {
  return (
    <div className="flex items-center gap-3 border-b border-zinc-100 bg-amber-50/30 px-6 py-2">
      <span className="inline-flex items-center gap-1.5 whitespace-nowrap text-[11px] font-semibold text-amber-700">
        <Icons.zap className="h-3.5 w-3.5" />Auto-score line
      </span>
      <span className="h-px flex-1 border-t border-dashed border-zinc-300" />
      <span className="hidden whitespace-nowrap text-[11px] text-zinc-400 sm:inline">above: scored automatically · below: watched</span>
    </div>
  );
}
window.AutoScoreLine = AutoScoreLine;

// A light "how is this scored" hint for the panel header — click to peek the rubric.
function IntentInfo() {
  const [open, setOpen] = useState(false);
  return (
    <div className="relative">
      <button onClick={() => setOpen((o) => !o)}
        className="inline-flex items-center gap-1 text-[12px] font-medium text-zinc-400 transition-colors hover:text-zinc-600">
        <Icons.info className="h-3.5 w-3.5" />How intent is scored
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-20" onClick={() => setOpen(false)} />
          <div className="absolute left-0 z-30 mt-2 w-[290px] rounded-xl border border-zinc-200 bg-white p-4 text-[12px] leading-relaxed text-zinc-600 shadow-lg shadow-zinc-900/5">
            <div className="mb-1.5 text-[12.5px] font-semibold text-zinc-800">How intent is scored</div>
            <p className="text-zinc-500">Deterministic — no AI. The strongest signal sets the base:</p>
            <div className="mt-1.5 space-y-1">
              <div className="flex justify-between"><span>New exec · exec engaged</span><span className="tabular-nums text-zinc-400">65 · 60</span></div>
              <div className="flex justify-between"><span>Revenue-cycle leader hire</span><span className="tabular-nums text-zinc-400">50</span></div>
              <div className="flex justify-between"><span>Core · standard RCM role</span><span className="tabular-nums text-zinc-400">30 · 18</span></div>
            </div>
            <p className="mt-2 text-zinc-500">then +15 per extra open role · +20 multi-signal · +20 ABM · +5 fresh</p>
            <p className="mt-2"><span className="font-medium text-amber-700">Hot ≥ 65</span> auto-scores · below is watched</p>
            <p className="mt-2.5 border-t border-zinc-100 pt-2 text-[11.5px] text-zinc-400">Next: tuned by your outcomes — engagement, meetings booked, deals won.</p>
          </div>
        </>
      )}
    </div>
  );
}
window.IntentInfo = IntentInfo;

function CompanyRow({ company, leaving, selected, onToggleSelect, onOpen, onPromote, onReject }) {
  const stop = (fn) => (e) => { e.stopPropagation(); fn(); };
  return (
    <div
      onClick={onOpen}
      className={`group relative cursor-pointer border-b border-zinc-100 px-6 transition-all duration-300 hover:bg-zinc-50/70
        ${selected ? 'bg-indigo-50/40' : ''}
        ${leaving ? 'max-h-0 -translate-x-2 overflow-hidden border-b-0 py-0 opacity-0' : 'max-h-[200px] py-4 opacity-100'}`}>
      <div className="flex items-center gap-4">
        {/* Select for bulk delete */}
        {onToggleSelect && (
          <input type="checkbox" checked={!!selected}
            onClick={(e) => e.stopPropagation()}
            onChange={(e) => { e.stopPropagation(); onToggleSelect(); }}
            className="h-4 w-4 shrink-0 cursor-pointer rounded border-zinc-300 text-indigo-600 focus:ring-indigo-300" />
        )}
        {/* Left: identity + signals */}
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2.5">
            <h3 className="truncate text-[15px] font-semibold text-zinc-900">{company.name}</h3>
            <SegmentBadge segment={company.segment} />
            <AbmBadge match={company.abm_match} />
          </div>
          <div className="mt-2 flex flex-wrap items-center gap-1.5">
            <SignalChips signals={company.signals} />
          </div>
          <div className="mt-2 flex flex-wrap items-center gap-1.5 text-[12px] text-zinc-400">
            <span>{company.signal_count} {company.signal_count === 1 ? 'signal' : 'signals'}</span>
            {company.qualified_at && (
              <>
                <span className="text-zinc-300">·</span>
                <span title={company.qualified_at}>evaluated {formatDateTime(company.qualified_at)}</span>
              </>
            )}
            {company.qualify_cost_usd != null && company.qualify_cost_usd > 0 && (
              <>
                <span className="text-zinc-300">·</span>
                <span className="tabular-nums text-zinc-500">${company.qualify_cost_usd.toFixed(2)}</span>
              </>
            )}
            {company.approximate_employees && (
              <>
                <span className="text-zinc-300">·</span>
                <span>~{company.approximate_employees.toLocaleString()} staff</span>
              </>
            )}
          </div>
        </div>

        {/* Middle: buying intent — the bar + score + tier, the ranking at a glance */}
        <IntentMeter tier={company.intent_tier} score={company.intent_score} />

        {/* Right: actions */}
        <div className="flex shrink-0 items-center gap-1.5">
          <div className="flex items-center gap-1.5 opacity-0 transition-opacity duration-150 group-hover:opacity-100">
            <RejectButton onClick={stop(onReject)} />
          </div>
          <PromoteButton onClick={stop(onPromote)} />
          <span className="ml-1 text-zinc-300 transition-colors group-hover:text-zinc-500">
            <Icons.arrowRight className="h-4 w-4" />
          </span>
        </div>
      </div>
    </div>
  );
}
window.CompanyRow = CompanyRow;

// ── Filters ─────────────────────────────────────────────────────────────────
function Dropdown({ label, value, options, onChange }) {
  return (
    <label className="inline-flex items-center gap-2 text-[13px] text-zinc-500">
      {label}
      <div className="relative">
        <select
          value={value}
          onChange={(e) => onChange(e.target.value)}
          className="appearance-none rounded-lg border border-zinc-200 bg-white py-1.5 pl-3 pr-8 text-[13px] font-medium text-zinc-800 transition-colors hover:bg-zinc-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-zinc-200">
          {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>
        <Icons.chevron className="pointer-events-none absolute right-2 top-1/2 h-4 w-4 -translate-y-1/2 text-zinc-400" />
      </div>
    </label>
  );
}
window.Dropdown = Dropdown;

// ── RejectReasonModal ───────────────────────────────────────────────────────
const REJECT_REASONS = ['Too small', 'Wrong segment', 'Already a customer', 'Bad fit'];
function RejectReasonModal({ company, onCancel, onConfirm }) {
  const [selected, setSelected] = useState(null);
  const [text, setText] = useState('');
  const reason = text.trim() || selected;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-zinc-900/30 backdrop-blur-[2px] animate-fade" onClick={onCancel} />
      <div className="relative w-full max-w-md rounded-2xl border border-zinc-200 bg-white p-6 shadow-xl animate-pop">
        <h3 className="text-[16px] font-semibold text-zinc-900">Reject {company.name}?</h3>
        <p className="mt-1 text-[13px] text-zinc-500">Pick a reason so the pipeline can learn. This is stored with the verdict.</p>
        <div className="mt-4 flex flex-wrap gap-2">
          {REJECT_REASONS.map((r) => (
            <button key={r}
              onClick={() => { setSelected(r); setText(''); }}
              className={`rounded-lg px-3 py-1.5 text-[13px] font-medium ring-1 ring-inset transition-colors
                ${selected === r && !text ? 'bg-zinc-900 text-white ring-zinc-900' : 'bg-white text-zinc-600 ring-zinc-200 hover:bg-zinc-50'}`}>
              {r}
            </button>
          ))}
        </div>
        <input
          value={text}
          onChange={(e) => { setText(e.target.value); setSelected(null); }}
          placeholder="Or write a custom reason…"
          className="mt-3 w-full rounded-lg border border-zinc-200 px-3 py-2 text-[13px] text-zinc-800 placeholder:text-zinc-400 focus:border-zinc-300 focus:outline-none focus-visible:ring-2 focus-visible:ring-zinc-200" />
        <div className="mt-5 flex items-center justify-end gap-2">
          <button onClick={onCancel}
            className="rounded-lg px-3.5 py-2 text-[13px] font-medium text-zinc-500 transition-colors hover:bg-zinc-100 hover:text-zinc-700">
            Cancel
          </button>
          <button
            disabled={!reason}
            onClick={() => onConfirm(reason)}
            className="rounded-lg bg-rose-600 px-3.5 py-2 text-[13px] font-medium text-white transition-colors hover:bg-rose-700 disabled:cursor-not-allowed disabled:opacity-40">
            Reject company
          </button>
        </div>
      </div>
    </div>
  );
}
window.RejectReasonModal = RejectReasonModal;
