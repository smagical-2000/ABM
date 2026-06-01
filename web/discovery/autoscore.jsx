// ── Auto-scoring: a daily deadline at which any company still in the queue is
//    automatically promoted to scoring, so nothing stalls. ───────────────────
const { useState } = React;

// next occurrence of `hour`:00 (local) strictly after `fromTs`
function nextDeadline(hour, fromTs) {
  const d = new Date(fromTs);
  d.setHours(hour, 0, 0, 0);
  if (d.getTime() <= fromTs) d.setDate(d.getDate() + 1);
  return d.getTime();
}
window.nextDeadline = nextDeadline;

// tiered formatting + urgency
function countdownState(ms) {
  if (ms <= 0) return { tier: 'fired', label: '0:00' };
  const s = Math.floor(ms / 1000);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  let label;
  if (h > 0) label = `${h}h ${m}m`;
  else if (m >= 10) label = `${m}m`;
  else label = `${m}:${String(sec).padStart(2, '0')}`;
  let tier = 'calm';
  if (ms <= 10 * 60 * 1000) tier = 'urgent';
  else if (ms <= 60 * 60 * 1000) tier = 'soon';
  return { tier, label };
}
window.countdownState = countdownState;

function formatClock(ts) {
  return new Date(ts).toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });
}
function dayWord(ts) {
  const d = new Date(ts); const n = new Date();
  return d.getDate() === n.getDate() && d.getMonth() === n.getMonth() ? 'today' : 'tomorrow';
}

// ── Toggle switch ───────────────────────────────────────────────────────────
function Switch({ on, onChange }) {
  return (
    <button
      onClick={() => onChange(!on)}
      className={`relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition-colors ${on ? 'bg-indigo-600' : 'bg-zinc-200'}`}>
      <span className={`inline-block h-4 w-4 transform rounded-full bg-white shadow transition-transform ${on ? 'translate-x-4' : 'translate-x-0.5'}`} />
    </button>
  );
}

// ── Countdown pill (header) ─────────────────────────────────────────────────
function AutoScorePill({ enabled, remainingMs, onClick, active }) {
  if (!enabled) {
    return (
      <button onClick={onClick}
        className={`inline-flex items-center gap-2 rounded-lg border px-3 py-1.5 text-[13px] font-medium transition-colors ${active ? 'border-zinc-300 bg-zinc-50' : 'border-zinc-200 bg-white'} text-zinc-400 hover:bg-zinc-50`}>
        <Icons.clock className="h-4 w-4" />Auto-score off
      </button>
    );
  }
  const { tier, label } = countdownState(remainingMs);
  const tone = tier === 'urgent'
    ? 'border-rose-200 bg-rose-50 text-rose-700'
    : tier === 'soon'
      ? 'border-amber-200 bg-amber-50 text-amber-700'
      : 'border-indigo-200 bg-indigo-50 text-indigo-700';
  const dot = tier === 'urgent' ? 'bg-rose-500' : tier === 'soon' ? 'bg-amber-500' : 'bg-indigo-500';
  return (
    <button onClick={onClick}
      className={`inline-flex items-center gap-2 rounded-lg border px-3 py-1.5 text-[13px] font-medium transition-colors ${tone}`}>
      <span className="relative flex h-2 w-2">
        {tier === 'urgent' && <span className={`absolute inline-flex h-full w-full animate-ping rounded-full ${dot} opacity-60`} />}
        <span className={`relative inline-flex h-2 w-2 rounded-full ${dot}`} />
      </span>
      <span>Auto-score in <span className="tabular-nums">{label}</span></span>
    </button>
  );
}
window.AutoScorePill = AutoScorePill;

// ── Settings popover ────────────────────────────────────────────────────────
function AutoScorePopover({ enabled, onToggle, hour, onHour, deadline, queued, onPreview, onClose }) {
  return (
    <>
      <div className="fixed inset-0 z-40" onClick={onClose} />
      <div className="absolute right-0 top-full z-50 mt-2 w-80 rounded-xl border border-zinc-200 bg-white p-4 shadow-xl shadow-zinc-900/10 animate-pop">
        <div className="flex items-start justify-between gap-3">
          <div>
            <h3 className="text-[14px] font-semibold text-zinc-900">Automatic scoring</h3>
            <p className="mt-0.5 text-[12px] leading-relaxed text-zinc-500">
              Each day at the set time, any <span className="font-medium text-zinc-600">qualified</span> company still in the queue is promoted to scoring — so nothing stalls if you're away. Needs-review companies are never auto-scored.
            </p>
          </div>
          <Switch on={enabled} onChange={onToggle} />
        </div>

        <div className={`mt-4 space-y-3 transition-opacity ${enabled ? '' : 'pointer-events-none opacity-40'}`}>
          <label className="flex items-center justify-between text-[13px] text-zinc-600">
            Runs daily at
            <div className="relative">
              <select value={hour} onChange={(e) => onHour(Number(e.target.value))}
                className="appearance-none rounded-lg border border-zinc-200 bg-white py-1.5 pl-3 pr-8 text-[13px] font-medium text-zinc-800 hover:bg-zinc-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-zinc-200">
                {[12, 13, 14, 15, 16, 17, 18].map((h) => (
                  <option key={h} value={h}>{formatClock(new Date().setHours(h, 0, 0, 0))}</option>
                ))}
              </select>
              <Icons.chevron className="pointer-events-none absolute right-2 top-1/2 h-4 w-4 -translate-y-1/2 text-zinc-400" />
            </div>
          </label>

          <div className="rounded-lg bg-zinc-50 px-3 py-2.5 text-[12px] text-zinc-500">
            <div className="flex items-center gap-1.5 text-zinc-700">
              <Icons.clock className="h-3.5 w-3.5" />
              <span className="font-medium">Next run {dayWord(deadline)} at {formatClock(deadline)}</span>
            </div>
            <div className="mt-1">{queued} qualified {queued === 1 ? 'company' : 'companies'} will be auto-promoted unless you act first.</div>
          </div>
        </div>

        <div className="mt-3 border-t border-zinc-100 pt-3">
          <button onClick={onPreview}
            className="inline-flex w-full items-center justify-center gap-1.5 rounded-lg border border-zinc-200 bg-white px-3 py-2 text-[12px] font-medium text-zinc-600 transition-colors hover:bg-zinc-50 hover:text-indigo-600">
            <Icons.zap className="h-3.5 w-3.5" />Preview countdown (demo)
          </button>
          <p className="mt-1.5 text-center text-[11px] text-zinc-400">Fast-forwards to ~12s so you can watch it fire.</p>
        </div>
      </div>
    </>
  );
}
window.AutoScorePopover = AutoScorePopover;

// ── Urgency banner (inside list card) ───────────────────────────────────────
function AutoScoreBanner({ remainingMs, queued }) {
  const { label } = countdownState(remainingMs);
  return (
    <div className="flex items-center gap-2.5 border-b border-rose-100 bg-rose-50/70 px-6 py-2.5 text-[13px] text-rose-700">
      <Icons.zap className="h-4 w-4 shrink-0" />
      <span>
        Auto-scoring in <span className="font-semibold tabular-nums">{label}</span> — {queued} qualified {queued === 1 ? 'company' : 'companies'} will be promoted to scoring unless you act.
      </span>
    </div>
  );
}
window.AutoScoreBanner = AutoScoreBanner;
