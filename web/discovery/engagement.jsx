const { useState, useEffect, useRef } = React;

// ── Engagement console — Reply.io heat per tracked account ───────────────────
// Reads /api/engagement (+ /inbox, + /{id}). Three views: Accounts (ranked by
// heat), Inbox (per-touch feed), and a drawer (breakdown + contacts + timeline).
// Heat colors are semantic (Hot amber, Warm emerald, Some sky, Lower zinc) — the
// design-system console palette. Board-facing: no emoji, color encodes meaning.

const HEAT = {
  Hot:   { dot: 'bg-amber-500',   text: 'text-amber-700',   bar: 'bg-amber-500',   row: 'bg-amber-50/40' },
  Warm:  { dot: 'bg-emerald-500', text: 'text-emerald-700', bar: 'bg-emerald-500', row: '' },
  Some:  { dot: 'bg-sky-500',     text: 'text-sky-700',     bar: 'bg-sky-500',     row: '' },
  Lower: { dot: 'bg-zinc-300',    text: 'text-zinc-400',    bar: 'bg-zinc-300',    row: '' },
};
const heatOf = (tier) => HEAT[tier] || HEAT.Lower;

const KIND = {
  click:          { label: 'Click',   dot: 'bg-sky-400' },
  reply:          { label: 'Reply',   dot: 'bg-emerald-400' },
  meeting_booked: { label: 'Meeting', dot: 'bg-indigo-500' },
  podcast_lead:   { label: 'Podcast', dot: 'bg-violet-500' },
  opportunity:    { label: 'Opportunity', dot: 'bg-rose-500' },
  high_intent_lead: { label: 'High-intent lead', dot: 'bg-rose-500' },
};
const kindOf = (k) => KIND[k] || { label: k || 'Touch', dot: 'bg-zinc-300' };

const MATCH = {
  domain: { label: 'Domain', cls: 'text-emerald-600' },
  name:   { label: 'Name',   cls: 'text-sky-600' },
};

function HeatCell({ tier, score }) {
  const h = heatOf(tier);
  return (
    <span className="inline-flex items-center gap-2">
      <span className={`h-1.5 w-1.5 rounded-full ${h.dot}`} />
      <span className={`text-[13px] font-medium ${h.text}`}>{tier}</span>
      <span className={`w-7 text-right text-[15px] font-semibold tabular-nums ${h.text}`}>{score}</span>
    </span>
  );
}

function pct(v) { return (v === null || v === undefined) ? '—' : `${v}%`; }

// Group repeated touches by kind + day so the timeline reads "Click ×8 · May 28 ·
// +8 pts" instead of one identical row per contact.
// Timeline dates include the year — touches can span years (e.g. an SFDC lead from
// last summer next to a recent click), so a year-less "Jul 2 / Mar 18" reads as out
// of order even when it's correctly sorted oldest-first.
function timelineDate(iso) {
  return iso
    ? new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
    : '—';
}

function groupTimeline(events) {
  const m = new Map();
  events.forEach((e) => {
    const day = timelineDate(e.occurred_at);
    const key = `${e.kind}|${day}`;
    const g = m.get(key) || { kind: e.kind, day, count: 0, points: 0, ts: '' };
    g.count += 1;
    g.points += (e.points || 0);
    if ((e.occurred_at || '') > g.ts) g.ts = e.occurred_at || '';
    m.set(key, g);
  });
  // chronological, earliest first (oldest touch at the top of the timeline)
  return [...m.values()].sort((a, b) => (a.ts || '').localeCompare(b.ts || ''));
}

// ── Accounts ─────────────────────────────────────────────────────────────────
function AccountRow({ a, onOpen, pushToast }) {
  const h = heatOf(a.tier);
  const width = Math.min(100, Math.round(((a.score || 0) / 30) * 100));
  return (
    <div onClick={() => onOpen(a)}
      className={`grid cursor-pointer grid-cols-[1fr_150px_120px_90px_64px_92px] items-center gap-3 border-b border-zinc-100 px-6 py-3 transition-colors last:border-0 hover:bg-zinc-50/70 ${h.row}`}>
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <span className="truncate text-[14px] font-medium text-zinc-900">{a.name}</span>
          {a.segment && <SegmentBadge segment={a.segment} />}
          {(a.lists || []).includes('abm') && (
            <span className="shrink-0 rounded-md bg-amber-50 px-1.5 py-0.5 text-[10.5px] font-medium text-amber-700 ring-1 ring-inset ring-amber-100">ABM</span>
          )}
        </div>
        <div className="mt-0.5 truncate text-[12px] text-zinc-400">
          {a.contacts} contact{a.contacts === 1 ? '' : 's'}{a.domain ? ` · ${a.domain}` : ''}
        </div>
      </div>
      <HeatCell tier={a.tier} score={a.score} />
      <div className="h-1.5 rounded-full bg-zinc-100">
        <div className={`h-full rounded-full ${h.bar} opacity-60`} style={{ width: `${width}%` }} />
      </div>
      <div className="text-[12px] text-zinc-500 tabular-nums">
        {pct(a.open_rate)} open · {pct(a.reply_rate)} rep
      </div>
      <div className="text-right text-[12px] text-zinc-400">{a.last_touch ? relativeTime(a.last_touch) : '—'}</div>
      <div className="flex items-center justify-end gap-1.5">
        {a.tier === 'Hot' && (
          <button onClick={(e) => { e.stopPropagation(); pushToast('Activate to SDR — coming soon', 'muted'); }}
            className="rounded-md bg-amber-50 px-2 py-1 text-[11px] font-medium text-amber-700 ring-1 ring-inset ring-amber-200 hover:bg-amber-100">Activate</button>
        )}
        <Icons.arrowRight className="h-3.5 w-3.5 text-zinc-300" />
      </div>
    </div>
  );
}

function AccountsView({ accounts, onOpen, pushToast }) {
  if (!accounts.length) {
    return (
      <div className="flex flex-col items-center justify-center py-20 text-center">
        <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-zinc-100 text-zinc-400"><Icons.inbox className="h-7 w-7" /></div>
        <h3 className="mt-5 text-[15px] font-semibold text-zinc-900">No engaged accounts yet</h3>
        <p className="mt-1.5 max-w-xs text-[13px] text-zinc-500">Run a sync to pull Reply.io engagement and match it to your scored + ABM accounts.</p>
      </div>
    );
  }
  return (
    <>
      <div className="grid grid-cols-[1fr_150px_120px_90px_64px_92px] gap-3 border-b border-zinc-100 bg-zinc-50 px-6 py-2 text-[11px] font-semibold uppercase tracking-wide text-zinc-400">
        <div>Account</div><div>Heat</div><div>Score</div><div>Rates</div><div className="text-right">Last</div><div />
      </div>
      {accounts.map((a) => <AccountRow key={a.account_id} a={a} onOpen={onOpen} pushToast={pushToast} />)}
    </>
  );
}

// ── Inbox ────────────────────────────────────────────────────────────────────
function InboxView({ inbox }) {
  const events = (inbox && inbox.events) || [];
  if (!events.length) {
    return <div className="px-6 py-16 text-center text-[13px] text-zinc-400">No touches in the window yet.</div>;
  }
  return (
    <>
      {inbox.unresolved > 0 && (
        <div className="flex items-center gap-2 border-b border-amber-100 bg-amber-50/60 px-6 py-2.5 text-[12.5px] text-amber-800">
          <span className="h-1.5 w-1.5 rounded-full bg-amber-500" />
          <span><span className="font-medium">{inbox.unresolved}</span> contact{inbox.unresolved === 1 ? '' : 's'} couldn't be matched to an account — kept for review.</span>
        </div>
      )}
      <div className="grid grid-cols-[80px_1fr_180px_80px_44px_56px] gap-3 border-b border-zinc-100 bg-zinc-50 px-6 py-2 text-[11px] font-semibold uppercase tracking-wide text-zinc-400">
        <div>Touch</div><div>Company</div><div>Account</div><div>Match</div><div className="text-right">Pts</div><div className="text-right">When</div>
      </div>
      {events.map((e, i) => {
        const k = kindOf(e.kind);
        const m = MATCH[e.match_tier];
        return (
          <div key={`${e.account_id || 'u'}:${e.kind}:${e.occurred_at}:${i}`} className="grid grid-cols-[80px_1fr_180px_80px_44px_56px] items-center gap-3 border-b border-zinc-100 px-6 py-2.5 text-[13px] last:border-0 hover:bg-zinc-50/60">
            <div className="flex items-center gap-1.5"><span className={`h-1.5 w-1.5 rounded-full ${k.dot}`} /><span className="text-[12px] font-medium text-zinc-600">{k.label}</span></div>
            <div className="truncate text-zinc-700">{e.company || '—'}</div>
            <div className="truncate text-[12px] text-zinc-500">{e.account_name || (e.account_id ? e.account_id : <span className="italic text-amber-600">unresolved</span>)}</div>
            <div className={`text-[12px] ${m ? m.cls : 'text-amber-600'}`}>{m ? m.label : 'Unresolved'}</div>
            <div className="text-right text-[13px] font-semibold tabular-nums text-zinc-700">+{e.points}</div>
            <div className="text-right text-[12px] text-zinc-400">{e.occurred_at ? relativeTime(e.occurred_at) : '—'}</div>
          </div>
        );
      })}
    </>
  );
}

// ── Drawer ───────────────────────────────────────────────────────────────────
function EngagementDrawer({ account, onClose, pushToast }) {
  const [detail, setDetail] = useState(null);
  useEffect(() => {
    if (!account) return;
    setDetail(null);
    window.API.engagementAccount(account.account_id).then(setDetail).catch(() => setDetail({ events: [], contacts: [] }));
  }, [account && account.account_id]);
  if (!account) return null;
  const h = heatOf(account.tier);
  const events = (detail && detail.events) || [];
  const contacts = (detail && detail.contacts) || [];
  const breakdown = {};
  events.forEach((e) => { breakdown[e.kind] = (breakdown[e.kind] || 0) + (e.points || 0); });
  return (
    <div className="fixed inset-0 z-40">
      <div className="absolute inset-0 bg-zinc-900/15 animate-fade" onClick={onClose} />
      <aside className="absolute right-0 top-0 flex h-full w-full max-w-[440px] flex-col bg-white shadow-2xl">
        <div className="border-b border-zinc-100 px-6 py-5">
          <div className="flex items-start justify-between">
            <div>
              <h2 className="text-[18px] font-semibold tracking-tight text-zinc-900">{account.name}</h2>
              <div className="mt-1.5 flex items-center gap-2.5">
                {account.segment && <SegmentBadge segment={account.segment} />}
                <HeatCell tier={account.tier} score={account.score} />
              </div>
            </div>
            <button onClick={onClose} className="rounded-lg p-1.5 text-zinc-400 hover:bg-zinc-100"><Icons.x className="h-5 w-5" /></button>
          </div>
          <div className="mt-2 text-[12px] text-zinc-400">
            {pct(account.open_rate)} open rate · {pct(account.reply_rate)} reply rate · {account.contacts} contacts
          </div>
        </div>
        <div className="flex-1 overflow-y-auto px-6 py-5">
          <div className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-zinc-400">Score breakdown</div>
          <div className="overflow-hidden rounded-xl border border-zinc-100">
            {Object.entries(breakdown).sort((a, b) => b[1] - a[1]).map(([kind, pts]) => {
              const k = kindOf(kind);
              return (
                <div key={kind} className="flex items-center gap-3 border-b border-zinc-50 px-4 py-2.5 last:border-0">
                  <span className={`h-1.5 w-1.5 rounded-full ${k.dot}`} />
                  <span className="flex-1 text-[13px] text-zinc-600">{k.label}</span>
                  <span className="text-[13px] font-semibold tabular-nums text-zinc-700">+{pts}</span>
                </div>
              );
            })}
            <div className="flex items-center justify-between bg-zinc-50 px-4 py-2.5">
              <span className="text-[11px] font-semibold uppercase tracking-wide text-zinc-400">Total</span>
              <span className={`text-[15px] font-bold tabular-nums ${h.text}`}>{account.score}</span>
            </div>
          </div>

          <div className="mb-2 mt-6 text-[11px] font-semibold uppercase tracking-wide text-zinc-400">
            Contacts engaging<span className="ml-1.5 text-zinc-400">{contacts.length}</span>
          </div>
          {contacts.length === 0 ? (
            <div className="text-[13px] text-zinc-400">No contact detail.</div>
          ) : (
            <div className="flex flex-wrap items-center gap-1">
              {contacts.slice(0, 12).map((c, i) => (
                <span key={c.external_id || i} title={c.email || c.external_id}
                  className="flex h-7 w-7 items-center justify-center rounded-full bg-zinc-100 text-[11px] font-semibold text-zinc-500 ring-1 ring-inset ring-zinc-200">
                  {(c.email || '?').slice(0, 1).toUpperCase()}
                </span>
              ))}
              {contacts.length > 12 && (
                <span className="ml-1 text-[12px] text-zinc-400">+{contacts.length - 12} more</span>
              )}
            </div>
          )}

          <div className="mb-2 mt-6 text-[11px] font-semibold uppercase tracking-wide text-zinc-400">Engagement timeline</div>
          <div className="ml-1 border-l border-zinc-100">
            {events.length === 0 && <div className="pl-4 text-[13px] text-zinc-400">No touches.</div>}
            {groupTimeline(events).map((g, i) => {
              const k = kindOf(g.kind);
              return (
                <div key={`${g.kind}:${g.day}:${i}`} className="relative pb-3.5 pl-5">
                  <span className={`absolute -left-1 top-1 h-2 w-2 rounded-full ${k.dot} ring-2 ring-white`} />
                  <div className="text-[13px] text-zinc-700">
                    {k.label}{g.count > 1 && <span className="ml-1 text-zinc-400">×{g.count}</span>}
                  </div>
                  <div className="mt-0.5 flex flex-wrap gap-1.5 text-[12px] text-zinc-400">
                    <span>{g.day}</span>
                    <span className="text-zinc-300">·</span>
                    <span className="font-medium text-zinc-500">+{g.points} pts</span>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
        <div className="flex gap-2 border-t border-zinc-100 px-6 py-3.5">
          {account.tier === 'Hot' && (
            <button onClick={() => pushToast('Activate to SDR — coming soon', 'muted')}
              className="inline-flex flex-1 items-center justify-center gap-2 rounded-lg bg-amber-600 px-4 py-2.5 text-[13px] font-semibold text-white hover:bg-amber-700">
              <Icons.zap className="h-4 w-4" />Activate to SDR</button>
          )}
          <button onClick={() => pushToast('Log touch — coming soon', 'muted')}
            className="inline-flex items-center justify-center gap-2 rounded-lg bg-white px-4 py-2.5 text-[13px] font-medium text-zinc-600 ring-1 ring-inset ring-zinc-200 hover:bg-zinc-50">
            <Icons.calendar className="h-4 w-4" />Log touch</button>
        </div>
      </aside>
    </div>
  );
}

// ── shell ────────────────────────────────────────────────────────────────────
function EngagementView({ pushToast }) {
  const [data, setData] = useState(null);     // { accounts, last_sync, running }
  const [inbox, setInbox] = useState(null);   // { events, unresolved }
  const [tab, setTab] = useState('accounts');
  const [open, setOpen] = useState(null);
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const pollRef = useRef(null);

  const load = (soft) => {
    if (!soft) setLoading(true);
    Promise.all([window.API.engagement(), window.API.engagementInbox()])
      .then(([d, ib]) => { setData(d); setInbox(ib); })
      .catch((e) => pushToast(`Couldn't load engagement: ${e.message}`, 'danger'))
      .finally(() => setLoading(false));
  };
  useEffect(() => { load(); return () => pollRef.current && clearInterval(pollRef.current); }, []);

  const sync = async () => {
    setSyncing(true);
    try {
      const res = await window.API.syncEngagement();
      if (res && res.busy) { setSyncing(false); pushToast('A sync is already running.', 'muted'); return; }
      pushToast('Syncing Reply.io engagement…', 'success');
      let tries = 0;
      pollRef.current = setInterval(async () => {
        tries += 1;
        const d = await window.API.engagement().catch(() => null);
        if ((d && !d.running) || tries > 40) {
          clearInterval(pollRef.current); pollRef.current = null;
          setSyncing(false); load(true);
          if (d && !d.running) pushToast('Engagement synced', 'success');
        }
      }, 3000);
    } catch (e) { setSyncing(false); pushToast(`Sync failed: ${e.message}`, 'danger'); }
  };

  const accounts = (data && data.accounts) || [];
  const stats = {
    hot: accounts.filter((a) => a.tier === 'Hot').length,
    warm: accounts.filter((a) => a.tier === 'Warm').length,
    accounts: accounts.length,
    touches: (inbox && inbox.events ? inbox.events.length : 0),
    unresolved: (inbox && inbox.unresolved) || 0,
  };
  const lastSync = data && data.last_sync;

  return (
    <main className="mx-auto max-w-5xl px-8 py-8">
      <div className="mb-6 flex items-end justify-between gap-4">
        <div>
          <h1 className="text-[24px] font-semibold tracking-tight text-zinc-900">Engagement</h1>
          <p className="mt-1 text-[14px] text-zinc-500">
            Reply.io + podcast + Salesforce engagement matched to your scored + ABM accounts, scored into heat.
            {lastSync && lastSync.last_synced_at && <span> Last synced {relativeTime(lastSync.last_synced_at)}.</span>}
          </p>
        </div>
        <button onClick={sync} disabled={syncing}
          className="inline-flex shrink-0 items-center gap-2 rounded-lg bg-indigo-600 px-3.5 py-2 text-[13px] font-medium text-white shadow-sm transition-colors hover:bg-indigo-700 disabled:opacity-50">
          <Icons.refresh className={`h-4 w-4 ${syncing ? 'animate-spin' : ''}`} />{syncing ? 'Syncing…' : 'Sync'}
        </button>
      </div>

      <div className="mb-5 flex items-center gap-8 rounded-xl border border-zinc-200 bg-white px-6 py-4">
        {[['Hot', stats.hot, 'text-amber-700'], ['Warm', stats.warm, 'text-emerald-700'],
          ['Accounts', stats.accounts, 'text-zinc-900'], ['Touches', stats.touches, 'text-zinc-900'],
          ['Need resolution', stats.unresolved, stats.unresolved ? 'text-amber-700' : 'text-zinc-400']].map(([label, val, cls]) => (
          <div key={label}>
            <div className={`text-[22px] font-bold leading-none tabular-nums ${cls}`}>{val}</div>
            <div className="mt-1 text-[12px] text-zinc-400">{label}</div>
          </div>
        ))}
      </div>

      <div className="overflow-hidden rounded-2xl border border-zinc-200 bg-white shadow-sm shadow-zinc-900/[0.02]">
        <div className="flex items-stretch gap-1 border-b border-zinc-100 px-4">
          {[['accounts', `Accounts ${stats.accounts}`], ['inbox', `Inbox ${stats.touches}`]].map(([id, label]) => (
            <button key={id} onClick={() => setTab(id)}
              className={`relative px-3 py-2.5 text-[13px] font-medium transition-colors ${tab === id ? 'text-zinc-900' : 'text-zinc-400 hover:text-zinc-600'}`}>
              {label}
              {tab === id && <span className="absolute inset-x-0 bottom-0 h-0.5 rounded-full bg-zinc-900" />}
            </button>
          ))}
        </div>
        {loading ? (
          <div className="px-6 py-16 text-center text-[13px] text-zinc-400">Loading…</div>
        ) : tab === 'accounts' ? (
          <AccountsView accounts={accounts} onOpen={setOpen} pushToast={pushToast} />
        ) : (
          <InboxView inbox={inbox} />
        )}
      </div>

      {open && <EngagementDrawer account={open} onClose={() => setOpen(null)} pushToast={pushToast} />}
    </main>
  );
}
window.EngagementView = EngagementView;
