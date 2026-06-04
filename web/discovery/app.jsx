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

// ── Header nav switch (Discovery | Scored) ──────────────────────────────────
function NavSwitch({ view, onChange, scoredCount, pulse }) {
  const item = (key, label) => {
    const active = view === key;
    return (
      <button onClick={() => onChange(key)}
        className={`relative flex items-center gap-1.5 rounded-md px-3 py-1.5 text-[13px] font-medium transition-all ${active ? 'bg-white text-zinc-900 shadow-sm ring-1 ring-zinc-200/70' : 'text-zinc-500 hover:text-zinc-700'}`}>
        {label}
        {key === 'scored' && (
          <span className={`rounded-full px-1.5 py-0.5 text-[10.5px] tabular-nums transition-all ${active ? 'bg-indigo-100 text-indigo-700' : 'bg-zinc-200/70 text-zinc-500'} ${pulse ? 'ring-2 ring-indigo-300' : ''}`}>{scoredCount}</span>
        )}
      </button>
    );
  };
  return (
    <div className="flex items-center gap-0.5 rounded-lg bg-zinc-100/80 p-0.5">
      {item('discovery', 'Discovery')}
      {item('scored', 'Scored')}
    </div>
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
        <span className="text-[13px] font-medium text-indigo-700">Discovering — {sources}</span>
        <span className="text-indigo-300">·</span>
        <span className="text-[13px] tabular-nums text-indigo-600">
          {evaluated > 0 ? `${qualified} qualified of ${evaluated} evaluated` : 'scanning sources…'}
        </span>
        <span className="ml-auto hidden items-center gap-1.5 text-[12px] text-indigo-400 sm:flex">
          <Icons.refresh className="h-3.5 w-3.5 animate-spin" />updating live
        </span>
      </div>
    </div>
  );
}

// ════════════════════════════════════════════════════════════════════════════
// App shell: Discovery | Scored. Discovery is unchanged; Scored is wired to the
// scoring API. Promote moves a company from Discovery into Scored.
// ════════════════════════════════════════════════════════════════════════════
function App() {
  const [view, setView] = useState('discovery');
  const [toasts, setToasts] = useState([]);
  const toastId = useRef(0);
  function pushToast(message, tone = 'success') {
    const id = ++toastId.current;
    setToasts((t) => [...t, { id, message, tone }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 2600);
  }

  // Rubric definitions are the single source of truth — load once so the Scored
  // components read window.FRAMEWORKS rather than hardcoding bands/pillars.
  useEffect(() => {
    window.API.frameworks().then((f) => { window.FRAMEWORKS = f; }).catch(() => {});
  }, []);

  // Cross-talk: when a company is promoted, nudge the Scored view to refetch and
  // pulse the nav badge so the transition reads.
  const [scoredRefreshKey, setScoredRefreshKey] = useState(0);
  const [scoredCount, setScoredCount] = useState(0);
  const [navPulse, setNavPulse] = useState(false);
  function bumpScored() {
    setScoredRefreshKey((k) => k + 1);
    setNavPulse(true);
    setTimeout(() => setNavPulse(false), 1600);
  }

  // ── DISCOVERY state + logic (unchanged from the live panel) ─────────────────
  const [loading, setLoading] = useState(true);
  const [companies, setCompanies] = useState([]);
  const [stats, setStats] = useState({ panel_pending: 0, qualified: 0, needs_review: 0, total: 0 });
  const [segment, setSegment] = useState('all');
  const [signalType, setSignalType] = useState('all');
  const [tab, setTab] = useState('qualified');
  const [openKey, setOpenKey] = useState(null);
  const [leaving, setLeaving] = useState({});
  const [rejectFor, setRejectFor] = useState(null);

  const [autoEnabled, setAutoEnabled] = useState(false);
  const [scoreHour, setScoreHour] = useState(15);
  const [deadline, setDeadline] = useState(() => window.nextDeadline(15, Date.now()));
  const [now, setNow] = useState(Date.now());
  const [autoOpen, setAutoOpen] = useState(false);
  const stateRef = useRef({ companies: [], leaving: {} });
  stateRef.current = { companies, leaving };

  async function loadAll(soft = false) {
    if (!soft) setLoading(true);
    try {
      const [qualified, needsReview, deferred, s] = await Promise.all([
        window.API.panel({ status: 'qualified' }),
        window.API.panel({ status: 'needs_review' }),
        window.API.panel({ status: 'deferred' }),
        window.API.stats(),
      ]);
      const tagged = [
        ...qualified.map((c) => ({ ...c, bucket: 'qualified' })),
        ...needsReview.map((c) => ({ ...c, bucket: 'needs_review' })),
        ...deferred.map((c) => ({ ...c, bucket: 'deferred' })),
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

  const [activity, setActivity] = useState([]);
  const [feed, setFeed] = useState([]);
  const wasActiveRef = useRef(false);
  const lastSeenRef = useRef(null);
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
        const recent = (a && a.recent) || [];
        setActivity(active);
        if (lastSeenRef.current === null) {
          lastSeenRef.current = recent.length ? recent[0].at : '';
        } else {
          const fresh = [];
          for (const r of recent) { if (r.at > lastSeenRef.current) fresh.push(r); else break; }
          if (fresh.length) { lastSeenRef.current = fresh[0].at; fresh.reverse().forEach(pushFeedItem); }
        }
        if (active.length > 0) { wasActiveRef.current = true; loadAll(true); }
        else if (wasActiveRef.current) { wasActiveRef.current = false; loadAll(true); pushToast('Discovery run complete', 'success'); }
      } catch (_) { /* ignore */ }
    }
    poll();
    const id = setInterval(poll, 4000);
    return () => { alive = false; clearInterval(id); };
  }, []);

  function removeCompany(key) {
    setLeaving((l) => ({ ...l, [key]: true }));
    setTimeout(() => {
      setCompanies((cs) => cs.filter((c) => c.company_key !== key));
      setStats((s) => ({ ...s, panel_pending: Math.max(0, s.panel_pending - 1) }));
      setLeaving((l) => { const n = { ...l }; delete n[key]; return n; });
    }, 320);
  }

  async function handlePromote(key) {
    const c = companies.find((x) => x.company_key === key);
    if (openKey === key) setOpenKey(null);
    try {
      await window.API.promote(key);
      removeCompany(key);
      bumpScored();
      pushToast(`Promoted ${c ? c.name : 'company'} → Scoring`, 'success');
    } catch (e) { pushToast(`Promote failed: ${e.message}`, 'danger'); }
  }
  async function handleDefer(key) {
    const c = companies.find((x) => x.company_key === key);
    if (openKey === key) setOpenKey(null);
    try { await window.API.defer(key); removeCompany(key); pushToast(`Deferred ${c ? c.name : 'company'}`, 'muted'); }
    catch (e) { pushToast(`Defer failed: ${e.message}`, 'danger'); }
  }
  async function handleRestore(key) {
    const c = companies.find((x) => x.company_key === key);
    if (openKey === key) setOpenKey(null);
    setLeaving((l) => ({ ...l, [key]: true }));
    try {
      await window.API.restore(key);
      pushToast(`Restored ${c ? c.name : 'company'} to queue`, 'success');
      setTimeout(() => { loadAll(true); setLeaving((l) => { const n = { ...l }; delete n[key]; return n; }); }, 320);
    } catch (e) { setLeaving((l) => { const n = { ...l }; delete n[key]; return n; }); pushToast(`Restore failed: ${e.message}`, 'danger'); }
  }
  async function handleReject(key, reason) {
    const c = companies.find((x) => x.company_key === key);
    if (openKey === key) setOpenKey(null);
    setRejectFor(null);
    try { await window.API.reject(key, reason); removeCompany(key); pushToast(`Rejected ${c ? c.name : 'company'} · ${reason}`, 'danger'); }
    catch (e) { pushToast(`Reject failed: ${e.message}`, 'danger'); }
  }

  async function doAutoScore() {
    const { companies: cs, leaving: lv } = stateRef.current;
    const remaining = cs.filter((c) => c.bucket === 'qualified' && !lv[c.company_key]);
    if (remaining.length === 0) return;
    setOpenKey(null); setRejectFor(null);
    for (const c of remaining) {
      try { await window.API.promote(c.company_key); removeCompany(c.company_key); } catch (e) { /* leave it */ }
    }
    bumpScored();
    pushToast(`${remaining.length} ${remaining.length === 1 ? 'company' : 'companies'} promoted to Scoring`, 'success');
  }
  useEffect(() => {
    if (!autoEnabled) return;
    let fired = false;
    const id = setInterval(() => {
      const t = Date.now(); setNow(t);
      if (!fired && t >= deadline) { fired = true; doAutoScore(); setDeadline(window.nextDeadline(scoreHour, t + 1000)); }
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
  const deferredCount = companies.filter((c) => c.bucket === 'deferred' && !leaving[c.company_key]).length;
  const remainingMs = deadline - now;
  const queuedCount = qualifiedCount;
  const urgent = autoEnabled && remainingMs <= 10 * 60 * 1000 && queuedCount > 0;
  function changeHour(h) { setScoreHour(h); setDeadline(window.nextDeadline(h, Date.now())); }
  function previewCountdown() { setAutoEnabled(true); setDeadline(Date.now() + 12000); setAutoOpen(false); }

  const discovery = view === 'discovery';

  return (
    <div className="min-h-screen bg-[#fafafa] text-zinc-900">
      <header className="sticky top-0 z-30 border-b border-zinc-200/80 bg-[#fafafa]/85 backdrop-blur-md">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-8 py-4">
          <div className="flex items-center gap-3">
            <div className="flex items-center gap-2.5">
              <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-indigo-600 text-white">
                <Icons.sparkle className="h-4 w-4" />
              </div>
              <span className="text-[15px] font-semibold tracking-tight">Magical</span>
              <span className="text-zinc-300">/</span>
            </div>
            <NavSwitch view={view} onChange={setView} scoredCount={scoredCount} pulse={navPulse} />
          </div>
          <div className="flex items-center gap-3">
            {discovery ? (
              <>
                <span className="hidden text-[12px] lg:inline">
                  {activity.length > 0 ? (
                    <span className="inline-flex items-center gap-1.5 font-medium text-indigo-600">
                      <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-indigo-500"></span>Processing…
                    </span>
                  ) : (<span className="text-zinc-400">Live · {stats.total} surfaced</span>)}
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
              </>
            ) : (
              <span className="hidden text-[12px] text-zinc-400 lg:inline">{scoredCount} scored · auto-refreshing</span>
            )}
          </div>
        </div>
      </header>

      {discovery && activity.length > 0 && <ActivityBanner runs={activity} />}

      {discovery ? (
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
              <TabButton active={tab === 'deferred'} onClick={() => setTab('deferred')} label="Deferred" count={deferredCount} accent="indigo" />
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
                  onReject={() => setRejectFor(c)}
                  onRestore={() => handleRestore(c.company_key)} />
              ))
            )}
          </div>

          <p className="mt-4 text-center text-[12px] text-zinc-400">
            Sorted by most recently surfaced · Disqualified companies are filtered out upstream
          </p>
        </main>
      ) : (
        <ScoredView refreshKey={scoredRefreshKey} pushToast={pushToast} onCount={setScoredCount} />
      )}

      {discovery && (
        <CompanyDrawer company={openCompany} onClose={() => setOpenKey(null)}
          onPromote={() => handlePromote(openKey)} onDefer={() => handleDefer(openKey)} onReject={() => setRejectFor(openCompany)}
          onRestore={() => handleRestore(openKey)} />
      )}
      {discovery && rejectFor && (
        <RejectReasonModal company={rejectFor} onCancel={() => setRejectFor(null)} onConfirm={(reason) => handleReject(rejectFor.company_key, reason)} />
      )}
      {discovery && <ActivityFeed items={feed} />}

      <ToastStack toasts={toasts} />
    </div>
  );
}

// ════════════════════════════════════════════════════════════════════════════
// Scored view — accounts in the scoring phase, read from the API. Polls while
// any account is in flight so 'Scoring…' rows resolve to scores live.
// ════════════════════════════════════════════════════════════════════════════
function ScoredView({ refreshKey, pushToast, onCount }) {
  const [accounts, setAccounts] = useState([]);
  const [loading, setLoading] = useState(true);
  const [segF, setSegF] = useState('all');
  const [tierF, setTierF] = useState('all');
  const [sourceF, setSourceF] = useState('all');
  const [qaF, setQaF] = useState('all');
  const [openAcc, setOpenAcc] = useState(null);
  const [openLanding, setOpenLanding] = useState(null);
  const [importing, setImporting] = useState(false);

  async function load(soft = false) {
    try {
      const a = await window.API.scored();
      setAccounts(a);
    } catch (e) { if (!soft) pushToast(`Couldn't load scores: ${e.message}`, 'danger'); }
    finally { if (!soft) setLoading(false); }
  }
  useEffect(() => { load(); }, []);
  useEffect(() => { if (refreshKey) load(true); }, [refreshKey]);
  // Poll while anything is queued/scoring so it resolves live.
  useEffect(() => {
    let alive = true;
    async function poll() {
      try { const r = await window.API.scoringActivity(); if (alive && (r.active || []).length > 0) load(true); }
      catch (_) { /* ignore */ }
    }
    const id = setInterval(poll, 3500);
    poll();
    return () => { alive = false; clearInterval(id); };
  }, []);
  useEffect(() => { onCount(accounts.filter((a) => a.state === 'scored').length); }, [accounts]);

  async function handleScore(id) {
    const a = accounts.find((x) => x.account_id === id);
    setAccounts((prev) => prev.map((x) => (x.account_id === id ? { ...x, state: 'scoring' } : x)));
    pushToast(`Scoring ${a ? a.name : 'account'}…`, 'success');
    try { await window.API.scoreAccount(id); } catch (e) { pushToast(`Score failed: ${e.message}`, 'danger'); load(true); }
  }
  function handleImported(res) {
    setImporting(false);
    pushToast(`Importing ${res.imported} ${res.imported === 1 ? 'account' : 'accounts'} — scoring…`, 'success');
    load(true);
  }

  const scoredList = useMemo(() => {
    const band = (a) => (a.total != null ? window.tierFor(a.framework, a.total).band : null);
    const arr = accounts.filter((a) => {
      if (segF !== 'all' && a.segment !== segF) return false;
      if (sourceF !== 'all' && a.source !== sourceF) return false;
      if (tierF !== 'all') { if (a.state !== 'scored' || band(a) !== tierF) return false; }
      if (qaF !== 'all') { if (!a.qa || a.qa.status !== qaF) return false; }
      return true;
    });
    const order = { scoring: 0, queued: 1, scored: 2, error: 3 };
    return arr.sort((a, b) => {
      if (order[a.state] !== order[b.state]) return order[a.state] - order[b.state];
      const ra = a.total != null ? a.total / a.max_total : 0;
      const rb = b.total != null ? b.total / b.max_total : 0;
      return rb - ra;
    });
  }, [accounts, segF, tierF, sourceF, qaF]);

  const scoredOnly = accounts.filter((a) => a.state === 'scored');
  const totalScored = scoredOnly.length;
  const tier1Count = scoredOnly.filter((a) => window.tierFor(a.framework, a.total).band === 'high').length;
  const flaggedCount = scoredOnly.filter((a) => a.qa && a.qa.status !== 'verified').length;
  const avgFit = totalScored ? Math.round(scoredOnly.reduce((s, a) => s + a.total / a.max_total, 0) / totalScored * 100) : 0;
  const tierConflicts = scoredOnly.filter((a) => a.qa && a.qa.tier_changing).length;

  const openAccount = accounts.find((a) => a.account_id === openAcc) || null;
  const landingAccount = accounts.find((a) => a.account_id === openLanding) || null;
  const visible = scoredList.length;

  return (
    <>
      <main className="mx-auto max-w-6xl px-8 py-8">
        <div className="mb-6 flex items-end justify-between gap-4">
          <div>
            <h1 className="text-[24px] font-semibold tracking-tight text-zinc-900">Scored Accounts</h1>
            <p className="mt-1 text-[14px] text-zinc-500">Each account is scored on its segment rubric and independently QA'd. Open any one for the breakdown.</p>
          </div>
          <button onClick={() => setImporting(true)}
            className="inline-flex shrink-0 items-center gap-2 rounded-lg bg-indigo-600 px-3.5 py-2 text-[13px] font-medium text-white shadow-sm transition-colors hover:bg-indigo-700">
            <Icons.upload className="h-4 w-4" />Import accounts
          </button>
        </div>

        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <StatTile value={totalScored} label="Scored" emphasized />
          <StatTile value={tier1Count} label="High Fit / Tier 1" />
          <StatTile value={flaggedCount} label="Flagged by QA" />
          <StatTile value={`${avgFit}%`} label="Avg fit" />
        </div>

        {tierConflicts > 0 && (
          <div className="mt-4 flex items-center gap-2.5 rounded-xl border border-rose-200 bg-rose-50/70 px-4 py-3 text-[13px] text-rose-700">
            <Icons.alert className="h-4 w-4 shrink-0 text-rose-500" />
            <span><span className="font-semibold">{tierConflicts} tier-changing QA {tierConflicts === 1 ? 'discrepancy' : 'discrepancies'}.</span> The independent pass disagrees on a fact that moves the fit tier — open the account before routing.</span>
            <button onClick={() => setQaF('discrepancy')} className="ml-auto shrink-0 rounded-lg bg-white px-2.5 py-1 text-[12px] font-medium text-rose-700 ring-1 ring-inset ring-rose-200 transition-colors hover:bg-rose-50">Show flagged</button>
          </div>
        )}

        <div className="mt-8 overflow-hidden rounded-2xl border border-zinc-200 bg-white shadow-sm shadow-zinc-900/[0.02]">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-zinc-100 px-6 py-3.5">
            <div className="flex flex-wrap items-center gap-4">
              <Dropdown label="Segment" value={segF} onChange={setSegF}
                options={[{ value: 'all', label: 'All' }, { value: 'health_system', label: 'Health System' }, { value: 'specialty', label: 'Specialty' }, { value: 'payer', label: 'Payer' }]} />
              <Dropdown label="Tier" value={tierF} onChange={setTierF}
                options={[{ value: 'all', label: 'All' }, { value: 'high', label: 'High Fit / Tier 1' }, { value: 'medium', label: 'Medium / Tier 2' }, { value: 'low', label: 'Low / Tier 3' }, { value: 'out', label: 'Tier 4' }]} />
              <Dropdown label="Source" value={sourceF} onChange={setSourceF}
                options={[{ value: 'all', label: 'All' }, { value: 'discovery', label: 'Discovery' }, { value: 'csv', label: 'CSV import' }]} />
              <Dropdown label="QA" value={qaF} onChange={setQaF}
                options={[{ value: 'all', label: 'All' }, { value: 'verified', label: 'Verified' }, { value: 'discrepancy', label: 'Discrepancy' }, { value: 'unverifiable', label: 'Unverifiable' }]} />
            </div>
            <span className="text-[13px] text-zinc-400">{visible} {visible === 1 ? 'account' : 'accounts'} · sorted by fit</span>
          </div>

          {loading ? (
            <div className="animate-pulse">{Array.from({ length: 5 }).map((_, i) => <ScoredSkeletonRow key={i} />)}</div>
          ) : visible === 0 ? (
            <div className="flex flex-col items-center justify-center py-24 text-center">
              <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-zinc-100 text-zinc-400"><Icons.layers className="h-7 w-7" /></div>
              <h3 className="mt-5 text-[15px] font-semibold text-zinc-900">No scored accounts yet</h3>
              <p className="mt-1.5 max-w-xs text-[13px] text-zinc-500">Promote a company from Discovery, or import a CSV to start scoring.</p>
              <button onClick={() => setImporting(true)} className="mt-5 inline-flex items-center gap-2 rounded-lg bg-zinc-900 px-3.5 py-2 text-[13px] font-medium text-white transition-colors hover:bg-zinc-800">
                <Icons.upload className="h-4 w-4" />Import accounts
              </button>
            </div>
          ) : (
            scoredList.map((a) => (
              <ScoredRow key={a.account_id} account={a}
                onOpen={() => setOpenAcc(a.account_id)} onScore={() => handleScore(a.account_id)}
                onLanding={() => setOpenLanding(a.account_id)} />
            ))
          )}
        </div>
        <p className="mt-4 text-center text-[12px] text-zinc-400">Promoted accounts and CSV imports converge here · QA runs independently on every score</p>
      </main>

      <ScoreDrawer account={openAccount} onClose={() => setOpenAcc(null)}
        onRescore={() => { if (openAccount) handleScore(openAccount.account_id); setOpenAcc(null); }}
        onAddToList={() => pushToast(`Added ${openAccount ? openAccount.name : 'account'} to target list`, 'success')}
        onOpenLanding={() => openAccount && setOpenLanding(openAccount.account_id)} />

      <LandingPageModal account={landingAccount} onClose={() => setOpenLanding(null)} pushToast={pushToast} />

      {importing && <ImportModal onClose={() => setImporting(false)} onImported={handleImported} pushToast={pushToast} />}
    </>
  );
}

ReactDOM.createRoot(document.getElementById('root')).render(<App />);
