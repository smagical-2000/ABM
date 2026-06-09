const { useState, useEffect, useRef } = React;

// ── News tab — RCM / regulation market intelligence ──────────────────────────
// Industry context (CMS rules, prior auth, denials, eligibility, healthcare-AI),
// NOT per-company signals. Reads /api/news; Refresh pulls the latest headlines +
// a cheap AI "why it matters" tag. Board-facing: clean cards, no emoji.

const NEWS_TOPIC_CLS = {
  prior_auth: 'bg-sky-50 text-sky-700 ring-sky-100',
  denials: 'bg-rose-50 text-rose-700 ring-rose-100',
  rcm_ai: 'bg-indigo-50 text-indigo-700 ring-indigo-100',
  eligibility: 'bg-emerald-50 text-emerald-700 ring-emerald-100',
  policy: 'bg-amber-50 text-amber-700 ring-amber-100',
  operations: 'bg-zinc-100 text-zinc-600 ring-zinc-200',
};

function NewsTopicChip({ topic, labels }) {
  const cls = NEWS_TOPIC_CLS[topic] || 'bg-zinc-100 text-zinc-600 ring-zinc-200';
  return (
    <span className={`shrink-0 rounded-md px-2 py-0.5 text-[11px] font-medium ring-1 ring-inset ${cls}`}>
      {(labels && labels[topic]) || topic}
    </span>
  );
}

function NewsCard({ item, labels }) {
  return (
    <div className="border-b border-zinc-100 px-6 py-4 transition-colors last:border-0 hover:bg-zinc-50/60">
      <div className="mb-1.5 flex items-center gap-2 text-[12px] text-zinc-400">
        {item.source && <span className="font-medium text-zinc-500">{item.source}</span>}
        {item.published_at && (
          <>
            <span className="text-zinc-300">·</span>
            <span title={formatDateTime(item.published_at)}>{relativeTime(item.published_at)}</span>
          </>
        )}
        {item.topic && <span className="ml-auto"><NewsTopicChip topic={item.topic} labels={labels} /></span>}
      </div>
      <a href={item.url} target="_blank" rel="noreferrer"
        className="block text-[15px] font-semibold leading-snug text-zinc-900 underline-offset-2 hover:text-indigo-600 hover:underline">
        {item.title}
      </a>
      {item.why_it_matters && (
        <div className="mt-2 flex items-start gap-2 rounded-lg bg-amber-50/60 px-3 py-2 text-[12.5px] text-amber-800 ring-1 ring-inset ring-amber-100">
          <Icons.zap className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-500" />
          <span><span className="font-semibold">Why it matters:</span> {item.why_it_matters}</span>
        </div>
      )}
    </div>
  );
}

function NewsView({ pushToast }) {
  const [data, setData] = useState(null);        // { items, topics, labels, last_run }
  const [topic, setTopic] = useState('all');
  const [days, setDays] = useState(30);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const pollRef = useRef(null);

  const query = () => ({ topic: topic === 'all' ? undefined : topic, days });

  const load = (soft) => {
    if (!soft) setLoading(true);
    window.API.news(query())
      .then(setData)
      .catch((e) => { if (!soft) pushToast(`Couldn't load news: ${e.message}`, 'danger'); })
      .finally(() => setLoading(false));
  };
  useEffect(() => { load(); }, [topic, days]);
  useEffect(() => () => { if (pollRef.current) clearInterval(pollRef.current); }, []);

  const refresh = async () => {
    setRefreshing(true);
    const startedAt = data && data.last_run && data.last_run.at;
    try {
      const res = await window.API.refreshNews();
      if (res && res.busy) { setRefreshing(false); pushToast('A news refresh is already running.', 'muted'); return; }
      pushToast('Pulling the latest headlines…', 'success');
      let tries = 0;
      pollRef.current = setInterval(async () => {
        tries += 1;
        try {
          const d = await window.API.news(query());
          const at = d.last_run && d.last_run.at;
          if ((at && at !== startedAt) || tries > 20) {
            clearInterval(pollRef.current); pollRef.current = null;
            setData(d); setRefreshing(false);
            if (at && at !== startedAt) {
              pushToast(`News updated — ${d.last_run.new || 0} new`, 'success');
            }
          }
        } catch (_) { /* keep polling */ }
      }, 3000);
    } catch (e) { setRefreshing(false); pushToast(`Refresh failed: ${e.message}`, 'danger'); }
  };

  const items = (data && data.items) || [];
  const labels = (data && data.labels) || {};
  const topics = (data && data.topics) || [];

  return (
    <main className="mx-auto max-w-4xl px-8 py-8">
      <div className="mb-6 flex items-end justify-between gap-4">
        <div>
          <h1 className="text-[24px] font-semibold tracking-tight text-zinc-900">RCM &amp; regulation news</h1>
          <p className="mt-1 text-[14px] text-zinc-500">
            Market intelligence — CMS rules, prior auth, denials, eligibility, healthcare-AI. Timing and talking points for outreach.
          </p>
        </div>
        <button onClick={refresh} disabled={refreshing}
          className="inline-flex shrink-0 items-center gap-2 rounded-lg bg-indigo-600 px-3.5 py-2 text-[13px] font-medium text-white shadow-sm transition-colors hover:bg-indigo-700 disabled:opacity-50">
          <Icons.refresh className={`h-4 w-4 ${refreshing ? 'animate-spin' : ''}`} />{refreshing ? 'Pulling…' : 'Refresh'}
        </button>
      </div>

      <div className="mb-4 flex flex-wrap items-center gap-2">
        {[['all', 'All'], ...topics.map((t) => [t, labels[t] || t])].map(([v, lbl]) => (
          <button key={v} onClick={() => setTopic(v)}
            className={`rounded-full px-3 py-1 text-[12.5px] font-medium ring-1 ring-inset transition-colors ${topic === v ? 'bg-zinc-900 text-white ring-zinc-900' : 'bg-white text-zinc-600 ring-zinc-200 hover:bg-zinc-50'}`}>
            {lbl}
          </button>
        ))}
        <span className="ml-auto">
          <Dropdown label="Window" value={String(days)} onChange={(v) => setDays(Number(v))}
            options={[{ value: '7', label: 'Last 7 days' }, { value: '30', label: 'Last 30 days' }, { value: '90', label: 'Last 90 days' }]} />
        </span>
      </div>

      <div className="overflow-hidden rounded-2xl border border-zinc-200 bg-white shadow-sm shadow-zinc-900/[0.02]">
        {loading ? (
          <div className="px-6 py-16 text-center text-[13px] text-zinc-400">Loading…</div>
        ) : items.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-20 text-center">
            <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-zinc-100 text-zinc-400">
              <Icons.inbox className="h-7 w-7" />
            </div>
            <h3 className="mt-5 text-[15px] font-semibold text-zinc-900">No news yet</h3>
            <p className="mt-1.5 max-w-xs text-[13px] text-zinc-500">Click Refresh to pull the latest RCM and regulation headlines.</p>
          </div>
        ) : (
          items.map((it) => <NewsCard key={it.url} item={it} labels={labels} />)
        )}
      </div>
    </main>
  );
}
window.NewsView = NewsView;
