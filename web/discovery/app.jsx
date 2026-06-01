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
  const [autoEnabled, setAutoEnabled] = useState(false); // off by default — promotes are real now
  const [scoreHour, setScoreHour] = useState(15);
  const [deadline, setDeadline] = useState(() => window.nextDeadline(15, Date.now()));
  const [now, setNow] = useState(Date.now());
  const [autoOpen, setAutoOpen] = useState(false);
  const stateRef = useRef({ companies: [], leaving: {} });
  stateRef.current = { companies, leaving };

  // ── initial load: fetch real data from the API ──────────────────────────────
  async function loadAll() {
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
      pushToast(`Couldn't load: ${e.message}`, 'danger');
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => { loadAll(); }, []);

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
            <span className="hidden text-[12px] text-zinc-400 lg:inline">Live · {stats.total} surfaced</span>
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
                options={[{ value: 'all', label: 'All' }, { value: 'layoff', label: 'Layoff' }, { value: 'leadership_change', label: 'Leadership change' }, { value: 'acquisition', label: 'Acquisition' }]} />
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
    </div>
  );
}

ReactDOM.createRoot(document.getElementById('root')).render(<App />);
