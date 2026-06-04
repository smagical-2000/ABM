// ── Landing Page ─────────────────────────────────────────────────────────────
// The 1-pager that is auto-generated for every scored account. (Generation
// engine is out of scope for now — this renders a board-ready preview from the
// data we already hold.) Composed as a clean, print-feeling document.

function LandingPageModal({ account, onClose, pushToast }) {
  if (!account) return null;
  const a = account;
  const tier = a.tier || window.tierFor(a.framework, a.total);
  const st = window.BAND_STYLE[tier.band] || window.BAND_STYLE.low;
  const fw = window.FRAMEWORKS[a.framework];
  const pillars = window.pillarsFor(a);
  const facts = a.firmographics ? Object.entries(a.firmographics) : [];
  const dateStr = a.scored_at ? window.shortDate(a.scored_at) : '—';

  return (
    <div className="fixed inset-0 z-[60] flex items-start justify-center overflow-y-auto p-4 sm:p-8">
      <div className="absolute inset-0 bg-zinc-900/40 backdrop-blur-[3px] animate-fade" onClick={onClose} />

      <div className="relative my-auto w-full max-w-3xl animate-pop">
        {/* floating toolbar */}
        <div className="mb-3 flex items-center justify-between">
          <span className="inline-flex items-center gap-1.5 rounded-full bg-white/90 px-2.5 py-1 text-[11px] font-medium text-zinc-500 ring-1 ring-inset ring-zinc-200 backdrop-blur">
            <window.Icons.doc className="h-3.5 w-3.5 text-zinc-400" />Landing Page · auto-generated preview
          </span>
          <div className="flex items-center gap-2">
            <button onClick={() => pushToast && pushToast(`Landing Page for ${a.name} exported`, 'success')}
              className="inline-flex items-center gap-1.5 rounded-lg bg-white px-3 py-1.5 text-[12px] font-medium text-zinc-600 ring-1 ring-inset ring-zinc-200 transition-colors hover:bg-zinc-50 hover:text-indigo-700">
              <window.Icons.upload className="h-3.5 w-3.5" />Export
            </button>
            <button onClick={onClose}
              className="inline-flex items-center justify-center rounded-lg bg-white p-1.5 text-zinc-400 ring-1 ring-inset ring-zinc-200 transition-colors hover:bg-zinc-50 hover:text-zinc-700">
              <Icons.x className="h-4 w-4" />
            </button>
          </div>
        </div>

        {/* the document */}
        <article className="overflow-hidden rounded-2xl bg-white shadow-2xl shadow-zinc-900/15 ring-1 ring-zinc-200">
          {/* letterhead */}
          <div className="flex items-center justify-between border-b border-zinc-100 px-9 py-5">
            <div className="flex items-center gap-2.5">
              <div className="flex h-6 w-6 items-center justify-center rounded-md bg-indigo-600 text-white"><Icons.sparkle className="h-3.5 w-3.5" /></div>
              <span className="text-[14px] font-semibold tracking-tight text-zinc-900">Magical</span>
              <span className="text-zinc-300">·</span>
              <span className="text-[12px] uppercase tracking-[0.18em] text-zinc-400">Account Brief</span>
            </div>
            <span className="text-[12px] text-zinc-400">{dateStr}</span>
          </div>

          {/* hero */}
          <div className="flex items-start justify-between gap-6 px-9 pt-8 pb-7">
            <div className="min-w-0">
              <h1 className="text-[28px] font-semibold leading-tight tracking-tight text-zinc-900 text-pretty">{a.name}</h1>
              {a.domain && <div className="mt-1 text-[13px] text-zinc-400">{a.domain}</div>}
              <div className="mt-3 flex flex-wrap items-center gap-2">
                <SegmentBadge segment={a.segment} size="lg" />
                <TierBadge band={tier.band} label={tier.label} size="lg" />
                {a.qa && <QABadge status={a.qa.status} tierChanging={a.qa.tier_changing} size="lg" />}
              </div>
            </div>
            <div className="flex shrink-0 flex-col items-center">
              <ScoreRing total={a.total} max={a.max_total} band={tier.band} size="lg" />
              <div className="mt-2 text-[11px] uppercase tracking-wider text-zinc-400">Fit score</div>
            </div>
          </div>

          {/* pillars */}
          <div className="grid grid-cols-3 gap-px overflow-hidden border-y border-zinc-100 bg-zinc-100">
            {pillars.map((p) => {
              const ratio = p.max ? p.score / p.max : 0;
              return (
                <div key={p.key} className="bg-white px-6 py-5">
                  <div className="text-[10.5px] font-semibold uppercase tracking-wider text-zinc-400">{p.label}</div>
                  <div className="mt-1.5 flex items-baseline gap-0.5">
                    <span className="text-[24px] font-semibold tabular-nums text-zinc-900">{p.score}</span>
                    <span className="text-[13px] text-zinc-400">/{p.max}</span>
                  </div>
                  <div className="mt-2.5 h-1.5 overflow-hidden rounded-full bg-zinc-100">
                    <div className="h-full rounded-full" style={{ width: `${ratio * 100}%`, background: st.ring }} />
                  </div>
                </div>
              );
            })}
          </div>

          {/* body */}
          <div className="grid grid-cols-5 gap-8 px-9 py-8">
            {/* recommendation */}
            <div className="col-span-3">
              <div className="text-[11px] font-semibold uppercase tracking-wider text-indigo-500">Recommendation</div>
              <p className="mt-2.5 text-[14px] leading-relaxed text-zinc-700 text-pretty">{a.recommendation}</p>

              {a.qa && (
                <div className="mt-5">
                  <div className="text-[11px] font-semibold uppercase tracking-wider text-zinc-400">Independent QA</div>
                  <p className="mt-2 text-[13px] leading-relaxed text-zinc-500 text-pretty">{a.qa.notes}</p>
                </div>
              )}
            </div>

            {/* facts */}
            <div className="col-span-2">
              <div className="text-[11px] font-semibold uppercase tracking-wider text-zinc-400">Key facts</div>
              <dl className="mt-2.5 rounded-xl border border-zinc-200">
                {facts.length === 0 && <div className="px-3.5 py-3 text-[13px] text-zinc-400">No structured facts yet.</div>}
                {facts.map(([k, v], i) => (
                  <div key={k} className={`flex items-baseline justify-between gap-3 px-3.5 py-2.5 ${i < facts.length - 1 ? 'border-b border-zinc-100' : ''}`}>
                    <dt className="text-[12px] text-zinc-400">{k}</dt>
                    <dd className="text-right text-[12.5px] font-medium text-zinc-800">{v}</dd>
                  </div>
                ))}
              </dl>
            </div>
          </div>

          {/* footer */}
          <div className="flex items-center justify-between gap-3 border-t border-zinc-100 bg-zinc-50/60 px-9 py-4 text-[11.5px] text-zinc-400">
            <span>Auto-generated by Magical · {fw ? `${fw.label} rubric ${fw.version}` : 'rubric'} · Sonnet</span>
            <span className="inline-flex items-center gap-1.5">
              <window.Icons.compass className="h-3.5 w-3.5" />{a.source === 'csv' ? 'CSV import' : 'Promoted from Discovery'}
            </span>
          </div>
        </article>
      </div>
    </div>
  );
}
window.LandingPageModal = LandingPageModal;
