const { useState, useEffect } = React;

// ── Watch list — companies parked by the jobs stacking gate ──────────────────
// A single open STANDARD RCM role (biller, coder, scheduler) is weak intent — not
// worth a paid qualification — so the gate PARKS the company here: stored, not
// scored, re-checked every run, and auto-qualified the moment a second role opens.
// This is the "stored in the DB but not qualified" list, made visible. No emoji.

function WatchRow({ c }) {
  const roles = (c.roles && c.roles.length) ? c.roles.join(' · ') : (c.role || 'RCM');
  const where = [c.city, c.state].filter(Boolean).join(', ');
  const many = c.postings && c.postings > 1;
  return (
    <div className="border-b border-zinc-100 px-6 py-4 transition-colors last:border-0 hover:bg-zinc-50/60">
      <div className="flex items-center justify-between gap-3">
        <h3 className="truncate text-[15px] font-semibold text-zinc-900">{c.name}</h3>
        {c.first_parked_at && (
          <span className="shrink-0 text-[12px] text-zinc-400"
            title={`Parked ${formatDateTime(c.first_parked_at)}`}>
            parked {relativeTime(c.first_parked_at)}
          </span>
        )}
      </div>
      <div className="mt-1.5 flex flex-wrap items-center gap-x-1.5 gap-y-1 text-[12.5px] text-zinc-500">
        <span className="inline-flex items-center gap-1 rounded-md bg-zinc-100 px-2 py-0.5 text-[11px] font-medium text-zinc-600 ring-1 ring-inset ring-zinc-200">
          <Icons.job className="h-3 w-3" />
          {many ? `${c.postings} standard postings` : '1 open standard role'}
        </span>
        <span className="text-zinc-600">{roles}</span>
        {where && (<><span className="text-zinc-300">·</span><span>{where}</span></>)}
      </div>
      {c.sample_title && (
        <div className="mt-1 truncate text-[12px] text-zinc-400">
          {c.sample_url
            ? <a href={c.sample_url} target="_blank" rel="noopener noreferrer"
                className="hover:text-indigo-600 hover:underline">{c.sample_title}</a>
            : c.sample_title}
        </div>
      )}
    </div>
  );
}

function WatchView({ pushToast }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    window.API.parked()
      .then((d) => { if (alive) setData(d); })
      .catch(() => { if (alive && pushToast) pushToast('Could not load the watch list'); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, []);

  const companies = (data && data.companies) || [];
  const stackMin = (data && data.stack_min) || 2;
  return (
    <main className="mx-auto max-w-4xl px-6 py-8">
      <div className="mb-1 flex items-baseline gap-3">
        <h2 className="text-[22px] font-semibold tracking-tight text-zinc-900">Watch list</h2>
        {data && <span className="text-[13px] text-zinc-400">{data.count} watched</span>}
      </div>
      <p className="mb-6 max-w-2xl text-[13.5px] leading-relaxed text-zinc-500">
        Companies with a single open <span className="font-medium text-zinc-600">standard</span> RCM
        role — stored, but not scored, so we don't spend qualifying low-intent hiring. Each is
        re-checked every run and auto-qualifies the moment a {stackMin}<sup>nd</sup> role opens or a
        stronger signal lands.
      </p>
      <div className="overflow-hidden rounded-xl border border-zinc-200 bg-white">
        {loading ? (
          <div className="px-6 py-12 text-center text-[13px] text-zinc-400">Loading…</div>
        ) : companies.length === 0 ? (
          <div className="px-6 py-14 text-center">
            <Icons.inbox className="mx-auto mb-3 h-6 w-6 text-zinc-300" />
            <p className="text-[13px] text-zinc-400">
              Nothing parked yet — single low-intent hires will collect here instead of
              burning qualify spend.
            </p>
          </div>
        ) : (
          companies.map((c) => <WatchRow key={c.company_key} c={c} />)
        )}
      </div>
    </main>
  );
}
window.WatchView = WatchView;
