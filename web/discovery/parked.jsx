const { useState, useEffect } = React;

// ── Watch list — low-intent leads kept out of Discovery ──────────────────────
// A single standard RCM role (biller, coder, scheduler) and nothing stronger is
// weak intent, so it doesn't belong in the main Discovery channel. Two kinds land
// here: QUALIFIED-but-low-intent leads (already evaluated, now filtered out of
// Discovery) and PARKED leads (never qualified — the gate skipped the spend). Both
// auto-promote back to Discovery the moment a 2nd role opens or a real signal lands.
// No emoji — board-facing.

// A qualified-but-low-intent lead: same readout as a Discovery row, read-only here.
function WatchLeadRow({ c }) {
  return (
    <div className="border-b border-zinc-100 px-6 py-4 transition-colors last:border-0 hover:bg-zinc-50/60">
      <div className="flex items-center gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2.5">
            <h3 className="truncate text-[15px] font-semibold text-zinc-900">{c.name}</h3>
            <SegmentBadge segment={c.segment} />
            <AbmBadge match={c.abm_match} />
          </div>
          <div className="mt-2 flex flex-wrap items-center gap-1.5">
            <SignalChips signals={c.signals} />
          </div>
          <div className="mt-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-[12px] text-zinc-400">
            <TtlHint action={c.ttl_action} days={c.ttl_days} />
            {c.qualified_at && (
              <span title={`Evaluated ${formatDateTime(c.qualified_at)}`}>
                {formatDateTime(c.qualified_at)}
              </span>
            )}
          </div>
        </div>
        <IntentMeter tier={c.intent_tier} score={c.intent_score} />
      </div>
    </div>
  );
}

// A parked lead: never qualified (no intent yet), so just the watch provenance.
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
          {safeHref(c.sample_url)
            ? <a href={safeHref(c.sample_url)} target="_blank" rel="noopener noreferrer"
                className="hover:text-indigo-600 hover:underline">{c.sample_title}</a>
            : c.sample_title}
        </div>
      )}
    </div>
  );
}

function WatchSection({ title, count, children }) {
  return (
    <section>
      <h3 className="mb-2 text-[12px] font-semibold uppercase tracking-wide text-zinc-400">
        {title} <span className="text-zinc-300">({count})</span>
      </h3>
      <div className="overflow-hidden rounded-xl border border-zinc-200 bg-white">{children}</div>
    </section>
  );
}

function WatchView({ pushToast }) {
  const [leads, setLeads] = useState(null);      // qualified, low intent (lone standard)
  const [parked, setParked] = useState(null);    // never qualified
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    Promise.all([window.API.watchlistLeads(), window.API.parked()])
      .then(([l, p]) => { if (alive) { setLeads(l || []); setParked(p || { companies: [] }); } })
      .catch(() => { if (alive && pushToast) pushToast('Could not load the watch list', 'danger'); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, []);

  const leadRows = leads || [];
  const parkedRows = (parked && parked.companies) || [];
  const total = leadRows.length + parkedRows.length;
  const stackMin = (parked && parked.stack_min) || 2;

  return (
    <main className="mx-auto max-w-4xl px-6 py-8">
      <div className="mb-1 flex items-baseline gap-3">
        <h2 className="text-[22px] font-semibold tracking-tight text-zinc-900">Watch list</h2>
        {!loading && <span className="text-[13px] text-zinc-400">{total} watched</span>}
      </div>
      <p className="mb-6 max-w-2xl text-[13.5px] leading-relaxed text-zinc-500">
        Low-intent leads — a single standard RCM role (biller, coder…) and nothing stronger.
        Kept out of Discovery so it stays high-signal, watched here with a TTL, and auto-promoted
        the moment a {stackMin}<sup>nd</sup> role opens or a real signal lands.
      </p>

      {loading ? (
        <div className="rounded-xl border border-zinc-200 bg-white px-6 py-12 text-center text-[13px] text-zinc-400">Loading…</div>
      ) : total === 0 ? (
        <div className="rounded-xl border border-zinc-200 bg-white px-6 py-14 text-center">
          <Icons.inbox className="mx-auto mb-3 h-6 w-6 text-zinc-300" />
          <p className="text-[13px] text-zinc-400">
            Nothing on the watch list — low-intent hires collect here instead of cluttering Discovery.
          </p>
        </div>
      ) : (
        <div className="space-y-6">
          {leadRows.length > 0 && (
            <WatchSection title="Qualified · low intent" count={leadRows.length}>
              {leadRows.map((c) => <WatchLeadRow key={c.company_key} c={c} />)}
            </WatchSection>
          )}
          {parkedRows.length > 0 && (
            <WatchSection title="Parked · not yet qualified" count={parkedRows.length}>
              {parkedRows.map((c) => <WatchRow key={c.company_key} c={c} />)}
            </WatchSection>
          )}
        </div>
      )}
    </main>
  );
}
window.WatchView = WatchView;
