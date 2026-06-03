const { useState, useEffect, useMemo, useRef } = React;

// ── Skeleton loading row ────────────────────────────────────────────────────
function SkeletonRow() {
  return (
    <div className="border-b border-zinc-100 px-6 py-4">
      <div className="flex items-center gap-4">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2.5">
            <div className="h-4 w-56 rounded bg-zinc-100" />
            <div className="h-4 w-24 rounded-full bg-zinc-100" />
          </div>
          <div className="mt-3 flex gap-1.5">
            <div className="h-6 w-32 rounded-md bg-zinc-100" />
            <div className="h-6 w-28 rounded-md bg-zinc-100" />
          </div>
          <div className="mt-3 h-3 w-40 rounded bg-zinc-100" />
        </div>
        <div className="hidden h-6 w-28 rounded bg-zinc-100 md:block" />
        <div className="h-8 w-24 rounded-lg bg-zinc-100" />
      </div>
    </div>
  );
}

function TabButton({ active, onClick, label, count, accent }) {
  return (
    <button onClick={onClick}
      className={`relative -mb-px flex items-center gap-2 px-3.5 py-2.5 text-[13px] font-medium transition-colors ${active ? 'text-zinc-900' : 'text-zinc-400 hover:text-zinc-600'}`}>
      {label}
      <span className={`rounded-full px-1.5 py-0.5 text-[11px] tabular-nums ${active ? (accent === 'amber' ? 'bg-amber-100 text-amber-700' : 'bg-indigo-100 text-indigo-700') : 'bg-zinc-100 text-zinc-400'}`}>{count}</span>
      {active && <span className="absolute inset-x-0 -bottom-px h-0.5 rounded-full bg-zinc-900"></span>}
    </button>
  );
}

// ── per-account decision ticker (fading corner feed) ────────────────────────
const DECISION_META = {
  qualified:    { label: 'Qualified',    icon: '✓', cls: 'text-emerald-600', bg: 'bg-emerald-50', ring: 'ring-emerald-200' },
  disqualified: { label: 'Disqualified', icon: '✕', cls: 'text-zinc-400',    bg: 'bg-zinc-50',    ring: 'ring-zinc-200' },
  needs_review: { label: 'Needs review', icon: '~', cls: 'text-amber-600',   bg: 'bg-amber-50',   ring: 'ring-amber-200' },
  error:        { label: 'Errored',      icon: '!', cls: 'text-rose-500',    bg: 'bg-rose-50',    ring: 'ring-rose-200' },
};

function ActivityFeedItem({ item }) {
  const [shown, setShown] = useState(false);
  useEffect(() => { const t = setTimeout(() => setShown(true), 20); return () => clearTimeout(t); }, []);
  const m = DECISION_META[item.status] || DECISION_META.disqualified;
  const visible = shown && !item.leaving;
  return (
    <div className={`flex items-center gap-2.5 rounded-xl border border-zinc-200/70 bg-white/95 px-3 py-2 shadow-lg shadow-zinc-900/5 backdrop-blur transition-all duration-500 ${visible ? 'translate-x-0 opacity-100' : '-translate-x-3 opacity-0'}`}>
      <span className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-[12px] font-bold ${m.bg} ${m.cls} ring-1 ring-inset ${m.ring}`}>{m.icon}</span>
      <div className="min-w-0">
        <div className="max-w-[200px] truncate text-[12.5px] font-medium text-zinc-800">{item.name}</div>
        <div className={`text-[11px] ${m.cls}`}>{m.label}{item.segment ? ` · ${item.segment.replace('_', ' ')}` : ''}</div>
      </div>
    </div>
  );
}

function ActivityFeed({ items }) {
  if (!items.length) return null;
  return (
    <div className="pointer-events-none fixed bottom-5 left-5 z-40 flex flex-col gap-2">
      {items.map((it) => <ActivityFeedItem key={it.id} item={it} />)}
    </div>
  );
}

// ── ActivityBanner — live "processing" marker shown while a run is in flight ─
function ActivityBanner({ runs }) {
  const qualified = runs.reduce((n, r) => n + (r.companies_qualified || 0), 0);
  const evaluated = runs.reduce((n, r) => n + (r.new_companies || 0), 0);
  const sources = [...new Set(runs.map((r) => r.source))].join(', ');
  return (
    <div className="border-b border-indigo-100 bg-gradient-to-r from-indigo-50 to-violet-50/50">
      <div className="mx-auto flex max-w-6xl items-center gap-3 px-8 py-2.5">
        <span className="relative flex h-2.5 w-2.5">
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-indigo-400 opacity-75"></span>
          <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-indigo-500"></span>
        </span>
        <span className="text-[13px] font-medium text-indigo-700">
          Discovering — {sources}
        </span>
        <span className="text-indigo-300">·</span>
        <span className="text-[13px] tabular-nums text-indigo-600">
          {evaluated > 0
            ? `${qualified} qualified of ${evaluated} evaluated`
            : 'scanning sources…'}
        </span>
        <span className="ml-auto hidden items-center gap-1.5 text-[12px] text-indigo-400 sm:flex">
          <Icons.refresh className="h-3.5 w-3.5 animate-spin" />updating live
        </span>
      </div>
    </div>
  );
}

function App() {
  const [loading, setLoading] = useState(true);
  const [companies, setCompanies] = useState([]);
  const [stats, setStats] = useState({ panel_pending: 0, qualified: 0, needs_review: 0, total: 0 });
  const [segment, setSegment] = useState('all');
  const [signalType, setSignalType] = useState('all');
  const [tab, setTab] = useState('qualified'); // 'qualified' | 'needs_review'
  const [openKey, setOpenKey] = useState(null);
  const [leaving, setLeaving] = useState({});
  const [rejectFor, setRejectFor] = useState(null);
  const [toasts, setToasts] = useState([]);
  const toastId = useRef(0);

  // ── auto-scoring ──────────────────────────────────────────────────────────
  // OFF by default. Promote currently persists review_status='promoted' to the
  // DB but does NOT create an accounts row or run scoring (it returns a stub
  // account id). Keep auto-bulk-promote off until real promotion + domain-first
  // matching exist, or the daily timer could promote dozens unreviewed.
  const [autoEnabled, setAutoEnabled] = useState(false);
  const [scoreHour, setScoreHour] = useState(15);
  const [deadline, setDeadline] = useState(() => window.nextDeadline(15, Date.now()));
  const [now, setNow] = useState(Date.now());
  const [autoOpen, setAutoOpen] = useState(false);
  const stateRef = useRef({ companies: [], leaving: {} });
  stateRef.current = { companies, leaving };

  // ── data load: fetch real data from the API ─────────────────────────────────
  // `soft` = a background refresh (polling while a run is live); it must not
  // flash the skeleton or toast on transient errors.
  async function loadAll(soft = false) {
    if (!soft) setLoading(true);
    try {
      const [qualified, needsReview, s] = await Promise.all([
        window.API.panel({ status: 'qualified' }),
        window.API.panel({ status: 'needs_review' }),
        window.API.stats(),
      ]);
      const tagged = [
        ...qualified.map((c) => ({ ...c, bucket: 'qualified' })),
        ...needsReview.map((c) => ({ ...c, bucket: 'needs_review' })),
      ].sort((a, b) => new Date(b.first_seen_at) - new Date(a.first_seen_at));
      setCompanies(tagged);
      setStats(s);
    } catch (e) {
      if (!soft) pushToast(`Couldn't load: ${e.message}`, 'danger');
    } finally {
      if (!soft) setLoading(false);
    }
  }
  useEffect(() => { loadAll(); }, []);

  // ── live activity: poll for in-progress runs + per-account decisions ─────────
  const [activity, setActivity] = useState([]);
  const [feed, setFeed] = useState([]);            // fading corner ticker
  const wasActiveRef = useRef(false);
  const lastSeenRef = useRef(null);                // newest decision ts already shown
  const feedIdRef = useRef(0);

  function pushFeedItem(entry) {
    const id = ++feedIdRef.current;
    setFeed((f) => [...f, { id, leaving: false, ...entry }].slice(-5));
    setTimeout(() => setFeed((f) => f.map((x) => (x.id === id ? { ...x, leaving: true } : x))), 5200);
    setTimeout(() => setFeed((f) => f.filter((x) => x.id !== id)), 5700);
  }

  useEffect(() => {
    let alive = true;
    async function poll() {
      try {
        const a = await window.API.activity();
        if (!alive) return;
        const active = (a && a.active) || [];
        const recent = (a && a.recent) || [];     // newest first
        setActivity(active);

        // Emit a fading ticker entry for each company decided since last poll.
        if (lastSeenRef.current === null) {
          lastSeenRef.current = recent.length ? recent[0].at : '';   // seed, don't spam history
        } else {
          const fresh = [];
          for (const r of recent) { if (r.at > lastSeenRef.current) fresh.push(r); else break; }
          if (fresh.length) {
            lastSeenRef.current = fresh[0].at;
            fresh.reverse().forEach(pushFeedItem);  // oldest → newest so they stack in order
          }
        }

        if (active.length > 0) {
          wasActiveRef.current = true;
          loadAll(true);                            // companies appear as they qualify
        } else if (wasActiveRef.current) {
          wasActiveRef.current = false;
          loadAll(true);
          pushToast('Discovery run complete', 'success');
        }
      } catch (_) { /* ignore transient poll errors */ }
    }
    poll();
    const id = setInterval(poll, 4000);
    return () => { alive = false; clearInterval(id); };
  }, []);

  function pushToast(message, tone = 'success') {
    const id = ++toastId.current;
    setToasts((t) => [...t, { id, message, tone }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 2600);
  }

  // optimistic removal with collapse animation
  function removeCompany(key) {
    setLeaving((l) => ({ ...l, [key]: true }));
    setTimeout(() => {
      setCompanies((cs) => cs.filter((c) => c.company_key !== key));
      setStats((s) => ({ ...s, panel_pending: Math.max(0, s.panel_pending - 1) }));
      setLeaving((l) => { const n = { ...l }; delete n[key]; return n; });
    }, 320);
  }

  // ── action handlers: call the API, then animate out (revert on failure) ─────
  async function handlePromote(key) {
    const c = companies.find((x) => x.company_key === key);
    if (openKey === key) setOpenKey(null);
    try {
      const { account_id } = await window.API.promote(key);
      removeCompany(key);
      pushToast(`Promoted ${c ? c.name : 'company'} → ${account_id}`, 'success');
    } catch (e) { pushToast(`Promote failed: ${e.message}`, 'danger'); }
  }
  async function handleDefer(key) {
    const c = companies.find((x) => x.company_key === key);
    if (openKey === key) setOpenKey(null);
    try {
      await window.API.defer(key);
      removeCompany(key);
      pushToast(`Deferred ${c ? c.name : 'company'}`, 'muted');
    } catch (e) { pushToast(`Defer failed: ${e.message}`, 'danger'); }
  }
  async function handleReject(key, reason) {
    const c = companies.find((x) => x.company_key === key);
    if (openKey === key) setOpenKey(null);
    setRejectFor(null);
    try {
      await window.API.reject(key, reason);
      removeCompany(key);
      pushToast(`Rejected ${c ? c.name : 'company'} · ${reason}`, 'danger');
    } catch (e) { pushToast(`Reject failed: ${e.message}`, 'danger'); }
  }

  // staggered auto-score: promote everything still qualified in the queue
  async function doAutoScore() {
    const { companies: cs, leaving: lv } = stateRef.current;
    const remaining = cs.filter((c) => c.bucket === 'qualified' && !lv[c.company_key]);
    if (remaining.length === 0) return;
    setOpenKey(null); setRejectFor(null);
    for (const c of remaining) {
      try { await window.API.promote(c.company_key); removeCompany(c.company_key); }
      catch (e) { /* leave it in the queue if it fails */ }
    }
    pushToast(`${remaining.length} ${remaining.length === 1 ? 'company' : 'companies'} auto-scored`, 'success');
  }

  useEffect(() => {
    if (!autoEnabled) return;
    let fired = false;
    const id = setInterval(() => {
      const t = Date.now();
      setNow(t);
      if (!fired && t >= deadline) {
        fired = true;
        doAutoScore();
        setDeadline(window.nextDeadline(scoreHour, t + 1000));
      }
    }, 200);
    return () => clearInterval(id);
  }, [autoEnabled, deadline, scoreHour]);

  const filtered = useMemo(() => companies.filter((c) => {
    if (c.bucket !== tab) return false;
    if (segment !== 'all' && c.segment !== segment) return false;
    if (signalType !== 'all' && !c.signals.some((s) => s.signal_type === signalType)) return false;
    return true;
  }), [companies, tab, segment, signalType]);

  const openCompany = companies.find((c) => c.company_key === openKey) || null;
  const visibleCount = filtered.filter((c) => !leaving[c.company_key]).length;
  const qualifiedCount = companies.filter((c) => c.bucket === 'qualified' && !leaving[c.company_key]).length;
  const needsCount = companies.filter((c) => c.bucket === 'needs_review' && !leaving[c.company_key]).length;
  const remainingMs = deadline - now;
  const queuedCount = qualifiedCount;
  const urgent = autoEnabled && remainingMs <= 10 * 60 * 1000 && queuedCount > 0;

  function changeHour(h) { setScoreHour(h); setDeadline(window.nextDeadline(h, Date.now())); }
  function previewCountdown() { setAutoEnabled(true); setDeadline(Date.now() + 12000); setAutoOpen(false); }

  return (
    <div className="min-h-screen bg-[#fafafa] text-zinc-900">
      <header className="sticky top-0 z-30 border-b border-zinc-200/80 bg-[#fafafa]/85 backdrop-blur-md">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-8 py-4">
          <div className="flex items-center gap-2.5">
            <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-indigo-600 text-white">
              <Icons.sparkle className="h-4 w-4" />
            </div>
            <span className="text-[15px] font-semibold tracking-tight">Magical</span>
            <span className="text-zinc-300">/</span>
            <span className="text-[15px] text-zinc-500">Discovery</span>
          </div>
          <div className="flex items-center gap-3">
            <span className="hidden text-[12px] lg:inline">
              {activity.length > 0 ? (
                <span className="inline-flex items-center gap-1.5 font-medium text-indigo-600">
                  <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-indigo-500"></span>
                  Processing…
                </span>
              ) : (
                <span className="text-zinc-400">Live · {stats.total} surfaced</span>
              )}
            </span>
            <div className="relative">
              <AutoScorePill enabled={autoEnabled} remainingMs={remainingMs} active={autoOpen} onClick={() => setAutoOpen((o) => !o)} />
              {autoOpen && (
                <AutoScorePopover enabled={autoEnabled} onToggle={setAutoEnabled} hour={scoreHour} onHour={changeHour}
                  deadline={deadline} queued={queuedCount} onPreview={previewCountdown} onClose={() => setAutoOpen(false)} />
              )}
            </div>
            <button onClick={() => { setLoading(true); loadAll(); pushToast('Refreshed', 'muted'); }}
              className="inline-flex items-center gap-2 rounded-lg border border-zinc-200 bg-white px-3 py-1.5 text-[13px] font-medium text-zinc-600 transition-colors hover:bg-zinc-50">
              <Icons.refresh className="h-4 w-4" />Refresh
            </button>
          </div>
        </div>
      </header>

      {activity.length > 0 && <ActivityBanner runs={activity} />}

      <main className="mx-auto max-w-6xl px-8 py-8">
        <div className="mb-6">
          <h1 className="text-[24px] font-semibold tracking-tight text-zinc-900">Discovery Panel</h1>
          <p className="mt-1 text-[14px] text-zinc-500">Review AI-qualified companies and route each one. Promote, defer, or reject.</p>
        </div>

        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <StatTile value={stats.panel_pending} label="In queue" emphasized />
          <StatTile value={stats.qualified} label="Qualified" />
          <StatTile value={stats.needs_review} label="Needs review" />
          <StatTile value={stats.total} label="Total surfaced" />
        </div>

        <div className="mt-8 overflow-hidden rounded-2xl border border-zinc-200 bg-white shadow-sm shadow-zinc-900/[0.02]">
          <div className="flex items-center gap-1 border-b border-zinc-100 px-4 pt-1.5">
            <TabButton active={tab === 'qualified'} onClick={() => setTab('qualified')} label="Qualified" count={qualifiedCount} accent="indigo" />
            <TabButton active={tab === 'needs_review'} onClick={() => setTab('needs_review')} label="Needs review" count={needsCount} accent="amber" />
          </div>

          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-zinc-100 px-6 py-3.5">
            <div className="flex flex-wrap items-center gap-4">
              <Dropdown label="Segment" value={segment} onChange={setSegment}
                options={[{ value: 'all', label: 'All' }, { value: 'health_system', label: 'Health System' }, { value: 'specialty', label: 'Specialty' }, { value: 'payer', label: 'Payer' }]} />
              <Dropdown label="Signal" value={signalType} onChange={setSignalType}
                options={[{ value: 'all', label: 'All' }, { value: 'job_posting', label: 'Hiring' }, { value: 'layoff', label: 'Layoff' }, { value: 'leadership_change', label: 'Leadership change' }, { value: 'acquisition', label: 'Acquisition' }, { value: 'funding_round', label: 'Funding' }]} />
            </div>
            <span className="text-[13px] text-zinc-400">
              {loading ? 'Loading…' : `${visibleCount} ${visibleCount === 1 ? 'company' : 'companies'}`}
            </span>
          </div>

          {!loading && tab === 'needs_review' && visibleCount > 0 && (
            <div className="flex items-center gap-2.5 border-b border-zinc-100 bg-zinc-50/70 px-6 py-2.5 text-[13px] text-zinc-500">
              <Icons.info className="h-4 w-4 shrink-0 text-zinc-400" />
              <span>The AI wasn't confident enough to qualify or disqualify these — each needs your manual decision.</span>
            </div>
          )}

          {!loading && urgent && tab === 'qualified' && <AutoScoreBanner remainingMs={remainingMs} queued={queuedCount} />}

          {loading ? (
            <div className="animate-pulse">{Array.from({ length: 4 }).map((_, i) => <SkeletonRow key={i} />)}</div>
          ) : visibleCount === 0 ? (
            <EmptyState variant={tab} onRun={() => pushToast('Discovery runs on a schedule', 'muted')} />
          ) : (
            filtered.map((c) => (
              <CompanyRow key={c.company_key} company={c} leaving={!!leaving[c.company_key]}
                onOpen={() => setOpenKey(c.company_key)}
                onPromote={() => handlePromote(c.company_key)}
                onDefer={() => handleDefer(c.company_key)}
                onReject={() => setRejectFor(c)} />
            ))
          )}
        </div>

        <p className="mt-4 text-center text-[12px] text-zinc-400">
          Sorted by most recently surfaced · Disqualified companies are filtered out upstream
        </p>
      </main>

      <CompanyDrawer company={openCompany} onClose={() => setOpenKey(null)}
        onPromote={() => handlePromote(openKey)} onDefer={() => handleDefer(openKey)} onReject={() => setRejectFor(openCompany)} />

      {rejectFor && (
        <RejectReasonModal company={rejectFor} onCancel={() => setRejectFor(null)}
          onConfirm={(reason) => handleReject(rejectFor.company_key, reason)} />
      )}

      <ToastStack toasts={toasts} />
      <ActivityFeed items={feed} />
    </div>
  );
}

ReactDOM.createRoot(document.getElementById('root')).render(<App />);
