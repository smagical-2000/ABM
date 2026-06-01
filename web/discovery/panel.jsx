const { useState } = React;

// ── Action buttons (shared between row and drawer) ──────────────────────────
function PromoteButton({ onClick, size = 'sm' }) {
  const pad = size === 'lg' ? 'px-4 py-2.5 text-[13px]' : 'px-3 py-1.5 text-[12px]';
  return (
    <button onClick={onClick}
      className={`inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 ${pad} font-medium text-white shadow-sm transition-colors hover:bg-indigo-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-300`}>
      <Icons.arrowUp className="h-3.5 w-3.5" />Promote
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
window.PromoteButton = PromoteButton;
window.DeferButton = DeferButton;
window.RejectButton = RejectButton;

// ── CompanyRow ──────────────────────────────────────────────────────────────
function CompanyRow({ company, leaving, onOpen, onPromote, onDefer, onReject }) {
  const stop = (fn) => (e) => { e.stopPropagation(); fn(); };
  return (
    <div
      onClick={onOpen}
      className={`group relative cursor-pointer border-b border-zinc-100 px-6 transition-all duration-300 hover:bg-zinc-50/70
        ${leaving ? 'max-h-0 -translate-x-2 overflow-hidden border-b-0 py-0 opacity-0' : 'max-h-[200px] py-4 opacity-100'}`}>
      <div className="flex items-center gap-4">
        {/* Left: identity + signals */}
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2.5">
            <h3 className="truncate text-[15px] font-semibold text-zinc-900">{company.name}</h3>
            <SegmentBadge segment={company.segment} />
          </div>
          <div className="mt-2 flex flex-wrap items-center gap-1.5">
            {company.signals.map((s, i) => <SignalChip key={i} signal={s} />)}
          </div>
          <div className="mt-2 flex items-center gap-1.5 text-[12px] text-zinc-400">
            <span>{company.signal_count} {company.signal_count === 1 ? 'signal' : 'signals'}</span>
            <span className="text-zinc-300">·</span>
            <span>seen {relativeTime(company.first_seen_at)}</span>
            {company.approximate_employees && (
              <>
                <span className="text-zinc-300">·</span>
                <span>~{company.approximate_employees.toLocaleString()} staff</span>
              </>
            )}
          </div>
        </div>

        {/* Middle: confidence */}
        <div className="hidden shrink-0 md:block">
          <div className="mb-1 text-right text-[11px] uppercase tracking-wide text-zinc-400">Confidence</div>
          <ConfidenceMeter value={company.confidence} />
        </div>

        {/* Right: actions */}
        <div className="flex shrink-0 items-center gap-1.5">
          <div className="flex items-center gap-1.5 opacity-0 transition-opacity duration-150 group-hover:opacity-100">
            <RejectButton onClick={stop(onReject)} />
            <DeferButton onClick={stop(onDefer)} />
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
