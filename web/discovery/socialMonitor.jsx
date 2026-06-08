// ── SocialMonitor — manage monitored LinkedIn accounts + run the poll ────────
// Lists the accounts whose post engagers we scrape (Magical's own = always on,
// competitors are add/removable), and a "Run now" button that scrapes the last
// 24h on demand. Qualified decision-makers stream onto the panel via the feed.

function SocialMonitor({ onClose, pushToast }) {
  const [targets, setTargets] = React.useState(null);
  const [url, setUrl] = React.useState('');
  const [label, setLabel] = React.useState('');
  const [busy, setBusy] = React.useState(false);

  const load = React.useCallback(() => {
    window.API.socialTargets()
      .then((d) => setTargets(d.targets || []))
      .catch(() => setTargets([]));
  }, []);
  React.useEffect(() => { load(); }, [load]);

  const add = async () => {
    const clean = url.trim();
    if (!clean) return;
    setBusy(true);
    try {
      await window.API.addSocialTarget({ linkedin_url: clean, label: label.trim() || null });
      setUrl(''); setLabel('');
      load();
    } catch (e) {
      pushToast(`Couldn't add account: ${e.message}`, 'danger');
    } finally {
      setBusy(false);
    }
  };

  const remove = async (t) => {
    try {
      await window.API.removeSocialTarget(t.linkedin_url);
      load();
    } catch (e) {
      pushToast(`Couldn't remove: ${e.message}`, 'danger');
    }
  };

  const competitors = (targets || []).filter((t) => t.kind !== 'own');
  const own = (targets || []).filter((t) => t.kind === 'own');

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-zinc-900/30 backdrop-blur-[2px] animate-fade" onClick={onClose} />
      <div className="relative w-full max-w-lg rounded-2xl border border-zinc-200 bg-white p-6 shadow-xl animate-pop">
        <div className="flex items-start justify-between gap-3">
          <div>
            <h3 className="text-[16px] font-semibold text-zinc-900">Monitored LinkedIn accounts</h3>
            <p className="mt-1 text-[13px] text-zinc-500">We scrape who engages with these accounts' posts, keep the decision-makers, and run them through ICP scoring.</p>
          </div>
          <button onClick={onClose} className="-mr-1.5 shrink-0 rounded-lg p-1.5 text-zinc-400 transition-colors hover:bg-zinc-100 hover:text-zinc-700">
            <Icons.x className="h-5 w-5" />
          </button>
        </div>

        {targets === null ? (
          <div className="mt-5 text-[13px] text-zinc-400">Loading…</div>
        ) : (
          <div className="mt-5 space-y-1.5">
            {own.map((t) => (
              <div key={t.linkedin_url} className="flex items-center justify-between gap-3 rounded-lg bg-amber-50/60 px-3 py-2 ring-1 ring-inset ring-amber-100">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-[13px] font-medium text-zinc-800">{t.label || 'Magical'}</span>
                    <span className="rounded-md bg-amber-100 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-amber-700">Own</span>
                  </div>
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
                <button onClick={() => remove(t)}
                  className="shrink-0 rounded-md px-2 py-1 text-[12px] font-medium text-zinc-400 opacity-0 transition-all hover:bg-rose-50 hover:text-rose-600 group-hover:opacity-100">
                  Remove
                </button>
              </div>
            ))}
            {competitors.length === 0 && (
              <p className="px-1 py-2 text-[12.5px] text-zinc-400">No competitor accounts yet — add one below.</p>
            )}
          </div>
        )}

        {/* Add */}
        <div className="mt-4 flex items-end gap-2 border-t border-zinc-100 pt-4">
          <div className="flex-1">
            <label className="text-[11px] font-medium uppercase tracking-wide text-zinc-400">LinkedIn URL</label>
            <input value={url} onChange={(e) => setUrl(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') add(); }}
              placeholder="linkedin.com/company/a-competitor"
              className="mt-1 w-full rounded-lg border border-zinc-200 px-3 py-2 text-[13px] text-zinc-800 placeholder:text-zinc-400 focus:border-zinc-300 focus:outline-none focus-visible:ring-2 focus-visible:ring-zinc-200" />
          </div>
          <div className="w-28">
            <label className="text-[11px] font-medium uppercase tracking-wide text-zinc-400">Label</label>
            <input value={label} onChange={(e) => setLabel(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') add(); }}
              placeholder="optional"
              className="mt-1 w-full rounded-lg border border-zinc-200 px-3 py-2 text-[13px] text-zinc-800 placeholder:text-zinc-400 focus:border-zinc-300 focus:outline-none focus-visible:ring-2 focus-visible:ring-zinc-200" />
          </div>
          <button onClick={add} disabled={busy || !url.trim()}
            className="rounded-lg bg-zinc-900 px-3.5 py-2 text-[13px] font-medium text-white transition-colors hover:bg-zinc-800 disabled:cursor-not-allowed disabled:opacity-40">
            Add
          </button>
        </div>

        {/* Footer — this panel only MANAGES the list; scanning happens via the
            one Run button (and the daily cron), so there's a single place to run. */}
        <div className="mt-5 flex items-center justify-between gap-2 border-t border-zinc-100 pt-4">
          <span className="text-[12px] text-zinc-400">
            Scanned every time you press <span className="font-medium text-zinc-500">Run</span> · and once daily
          </span>
          <button onClick={onClose}
            className="rounded-lg bg-zinc-900 px-4 py-2 text-[13px] font-medium text-white transition-colors hover:bg-zinc-800">
            Done
          </button>
        </div>
      </div>
    </div>
  );
}
window.SocialMonitor = SocialMonitor;
