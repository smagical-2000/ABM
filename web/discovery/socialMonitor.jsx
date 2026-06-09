// ── SocialMonitor — the Social Listening control center ──────────────────────
// One place to manage WHAT we listen to and run a controlled scan:
//   • Monitored accounts — whose post likes/comments we mine (Magical = always on)
//   • Event keywords — conferences whose posts we search for ATTENDEES
//   • Run scan — a manual run with a date window (24h / week / month)
// The main Run button + the daily cron always do the 24h window automatically;
// this panel is where you widen it (week/month back-fill) or run social alone.

function SocialMonitor({ onClose, pushToast }) {
  const [targets, setTargets] = React.useState(null);
  const [keywords, setKeywords] = React.useState(null);
  const [url, setUrl] = React.useState('');
  const [label, setLabel] = React.useState('');
  const [kw, setKw] = React.useState('');
  const [window_, setWindow] = React.useState('24h');
  const [busy, setBusy] = React.useState(false);
  const [running, setRunning] = React.useState(false);

  const load = React.useCallback(() => {
    window.API.socialTargets().then((d) => setTargets(d.targets || [])).catch(() => setTargets([]));
    window.API.eventKeywords().then((d) => setKeywords(d.keywords || [])).catch(() => setKeywords([]));
  }, []);
  React.useEffect(() => { load(); }, [load]);

  const addAccount = async () => {
    if (!url.trim()) return;
    setBusy(true);
    try {
      await window.API.addSocialTarget({ linkedin_url: url.trim(), label: label.trim() || null });
      setUrl(''); setLabel(''); load();
    } catch (e) { pushToast(`Couldn't add account: ${e.message}`, 'danger'); }
    finally { setBusy(false); }
  };
  const removeAccount = (t) =>
    window.API.removeSocialTarget(t.linkedin_url).then(load)
      .catch((e) => pushToast(`Couldn't remove: ${e.message}`, 'danger'));

  const addKeyword = async () => {
    if (kw.trim().length < 2) return;
    setBusy(true);
    try { await window.API.addEventKeyword({ keyword: kw.trim() }); setKw(''); load(); }
    catch (e) { pushToast(`Couldn't add keyword: ${e.message}`, 'danger'); }
    finally { setBusy(false); }
  };
  const removeKeyword = (k) =>
    window.API.removeEventKeyword(k.keyword).then(load)
      .catch((e) => pushToast(`Couldn't remove: ${e.message}`, 'danger'));

  const runScan = async () => {
    setRunning(true);
    let closing = false;
    try {
      const res = await window.API.runSocial({ window: window_, scope: 'all' });
      if (res.started) {
        closing = true;
        const bits = [res.accounts ? `${res.accounts} accounts` : null,
          res.keywords ? `${res.keywords} keywords` : null].filter(Boolean).join(' + ');
        pushToast(`Scanning ${bits || 'social'} (${res.window}) — results stream onto the panel.`, 'success');
        onClose();
      } else if (res.busy) pushToast('A run is already in progress.', 'muted');
      else if (res.budget_blocked) pushToast('Monthly budget reached — raise it or wait.', 'danger');
      else pushToast('Nothing active to scan — add an account or keyword first.', 'muted');
    } catch (e) { pushToast(`Run failed: ${e.message}`, 'danger'); }
    finally { if (!closing) setRunning(false); }
  };

  const own = (targets || []).filter((t) => t.kind === 'own');
  const competitors = (targets || []).filter((t) => t.kind !== 'own');
  const fieldCls = 'rounded-lg border border-zinc-200 px-3 py-2 text-[13px] text-zinc-800 placeholder:text-zinc-400 focus:border-zinc-300 focus:outline-none focus-visible:ring-2 focus-visible:ring-zinc-200';

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-zinc-900/30 backdrop-blur-[2px] animate-fade" onClick={onClose} />
      <div className="relative flex max-h-[88vh] w-full max-w-lg flex-col rounded-2xl border border-zinc-200 bg-white shadow-xl animate-pop">
        <div className="flex items-start justify-between gap-3 border-b border-zinc-100 px-6 pt-5 pb-4">
          <div>
            <h3 className="text-[16px] font-semibold text-zinc-900">Social listening</h3>
            <p className="mt-1 text-[13px] text-zinc-500">Mine post engagers + event attendees → decision-makers → ICP scoring.</p>
          </div>
          <button onClick={onClose} className="-mr-1.5 shrink-0 rounded-lg p-1.5 text-zinc-400 transition-colors hover:bg-zinc-100 hover:text-zinc-700">
            <Icons.x className="h-5 w-5" />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-6 py-4">
          {/* Monitored accounts */}
          <div className="text-[11px] font-semibold uppercase tracking-wider text-zinc-400">Monitored accounts · likes & comments</div>
          <div className="mt-2 space-y-1.5">
            {own.map((t) => (
              <div key={t.linkedin_url} className="flex items-center justify-between gap-3 rounded-lg bg-amber-50/60 px-3 py-2 ring-1 ring-inset ring-amber-100">
                <div className="min-w-0">
                  <div className="flex items-center gap-2"><span className="text-[13px] font-medium text-zinc-800">{t.label || 'Magical'}</span><span className="rounded-md bg-amber-100 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-amber-700">Own</span></div>
                  <div className="truncate text-[11.5px] text-zinc-400">{t.linkedin_url}</div>
                </div>
                <span className="text-[11px] text-zinc-400">always on</span>
              </div>
            ))}
            {competitors.map((t) => (
              <div key={t.linkedin_url} className="group flex items-center justify-between gap-3 rounded-lg px-3 py-2 hover:bg-zinc-50">
                <div className="min-w-0">
                  <div className="text-[13px] font-medium text-zinc-800">{t.label || t.linkedin_url.replace(/^https?:\/\/(www\.)?linkedin\.com\/(company|in)\//i, '')}</div>
                  <div className="truncate text-[11.5px] text-zinc-400">{t.linkedin_url}</div>
                </div>
                <button onClick={() => removeAccount(t)} className="shrink-0 rounded-md px-2 py-1 text-[12px] font-medium text-zinc-400 opacity-0 transition-all hover:bg-rose-50 hover:text-rose-600 group-hover:opacity-100">Remove</button>
              </div>
            ))}
            {targets !== null && competitors.length === 0 && <p className="px-1 py-1 text-[12.5px] text-zinc-400">No competitor accounts yet.</p>}
          </div>
          <div className="mt-2 flex items-end gap-2">
            <input value={url} onChange={(e) => setUrl(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter') addAccount(); }}
              placeholder="linkedin.com/company/a-competitor" className={`flex-1 ${fieldCls}`} />
            <input value={label} onChange={(e) => setLabel(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter') addAccount(); }}
              placeholder="label" className={`w-24 ${fieldCls}`} />
            <button onClick={addAccount} disabled={busy || !url.trim()} className="rounded-lg bg-zinc-900 px-3.5 py-2 text-[13px] font-medium text-white hover:bg-zinc-800 disabled:opacity-40">Add</button>
          </div>

          {/* Event keywords */}
          <div className="mt-6 text-[11px] font-semibold uppercase tracking-wider text-zinc-400">Event keywords · find attendees</div>
          <div className="mt-2 flex flex-wrap gap-1.5">
            {(keywords || []).map((k) => (
              <span key={k.keyword} className="group inline-flex items-center gap-1.5 rounded-full bg-fuchsia-50 px-2.5 py-1 text-[12px] font-medium text-fuchsia-700 ring-1 ring-inset ring-fuchsia-100">
                {k.keyword}
                <button onClick={() => removeKeyword(k)} className="text-fuchsia-400 hover:text-fuchsia-700" title="Remove"><Icons.x className="h-3 w-3" /></button>
              </span>
            ))}
            {keywords !== null && keywords.length === 0 && <p className="px-1 py-1 text-[12.5px] text-zinc-400">No event keywords yet — add a conference hashtag.</p>}
          </div>
          <div className="mt-2 flex items-end gap-2">
            <input value={kw} onChange={(e) => setKw(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter') addKeyword(); }}
              placeholder='e.g. "HIMSS26" or "BHT2026"' className={`flex-1 ${fieldCls}`} />
            <button onClick={addKeyword} disabled={busy || kw.trim().length < 2} className="rounded-lg bg-zinc-900 px-3.5 py-2 text-[13px] font-medium text-white hover:bg-zinc-800 disabled:opacity-40">Add</button>
          </div>
          <p className="mt-2 text-[11.5px] leading-relaxed text-zinc-400">We confirm the author actually attended (from the post text), keep US decision-makers, and run them through ICP scoring.</p>
        </div>

        {/* Footer — BACK-FILL, not a second run. The daily Run (and cron) already
            cover the last 24h; this is the catch-up for older posts (week/month). */}
        <div className="border-t border-zinc-100 px-6 py-4">
          <div className="mb-2 text-[11.5px] text-zinc-400">The <span className="font-medium text-zinc-500">Run</span> button + the daily cron already scan the last 24h. Use this only to back-fill older posts.</div>
          <div className="flex items-center justify-between gap-2">
            <div className="flex items-center gap-2 text-[12px] text-zinc-500">
              <span>Back-fill the last</span>
              <select value={window_} onChange={(e) => setWindow(e.target.value)}
                className="rounded-lg border border-zinc-200 bg-white py-1.5 pl-2.5 pr-7 text-[12.5px] font-medium text-zinc-800 hover:bg-zinc-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-zinc-200">
                <option value="24h">24 hours</option>
                <option value="week">week</option>
                <option value="month">month</option>
              </select>
            </div>
            <button onClick={runScan} disabled={running}
              className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-4 py-2 text-[13px] font-semibold text-white shadow-sm transition-colors hover:bg-indigo-700 disabled:cursor-not-allowed disabled:opacity-50">
              {running ? (<><Icons.refresh className="h-4 w-4 animate-spin" />Starting…</>) : (<><Icons.sparkle className="h-4 w-4" />Back-fill now</>)}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
window.SocialMonitor = SocialMonitor;
