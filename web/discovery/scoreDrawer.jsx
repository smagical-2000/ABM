// ── ScoreDrawer ──────────────────────────────────────────────────────────────
// Right-side slide-over mirroring CompanyDrawer. Renders the full per-dimension
// breakdown, the independent QA verdict, the recommendation, and provenance.

function ScoreField({ label, children }) {
  return (
    <div className="flex items-baseline justify-between gap-4 py-2.5 border-b border-zinc-100 last:border-0">
      <span className="shrink-0 text-[13px] text-zinc-400">{label}</span>
      <span className="text-right text-[13px] font-medium text-zinc-800">{children}</span>
    </div>
  );
}

function QAVerdict({ qa }) {
  const m = window.QA_META[qa.status];
  const Icon = window.Icons[m.icon];
  const tone = qa.tier_changing
    ? { wrap: 'border-rose-200 bg-rose-50/70', ic: 'text-rose-500', head: 'text-rose-800', body: 'text-rose-700' }
    : qa.status === 'verified'
      ? { wrap: 'border-emerald-200 bg-emerald-50/60', ic: 'text-emerald-500', head: 'text-emerald-800', body: 'text-emerald-700' }
      : qa.status === 'discrepancy'
        ? { wrap: 'border-amber-200 bg-amber-50/60', ic: 'text-amber-500', head: 'text-amber-800', body: 'text-amber-700' }
        : { wrap: 'border-zinc-200 bg-zinc-50/70', ic: 'text-zinc-400', head: 'text-zinc-700', body: 'text-zinc-500' };
  const heading = qa.tier_changing ? 'Tier-changing discrepancy' : m.label;
  return (
    <div className={`rounded-xl border px-4 py-3.5 ${tone.wrap}`}>
      <div className="flex items-center gap-2">
        <Icon className={`h-4 w-4 ${tone.ic}`} />
        <span className={`text-[13px] font-semibold ${tone.head}`}>{heading}</span>
        <span className="ml-auto inline-flex items-center gap-1 text-[11px] text-zinc-400">
          <window.Icons.sparkle className="h-3.5 w-3.5" />Independent pass
        </span>
      </div>
      <p className={`mt-2 text-[13px] leading-relaxed ${tone.body} text-pretty`}>{qa.notes}</p>
    </div>
  );
}

function ScoreDrawer({ account, onClose, onRescore, onAddToList, onOpenLanding }) {
  const open = !!account;
  const animate = typeof document === 'undefined' || document.visibilityState !== 'hidden';
  const a = account;
  const tier = a && a.total != null ? (a.tier || window.tierFor(a.framework, a.total)) : null;
  const fw = a ? window.FRAMEWORKS[a.framework] : null;
  // map corrections by dimension label for inline rendering
  const corrByDim = {};
  if (a && a.qa && a.qa.corrections) a.qa.corrections.forEach((c) => { corrByDim[c.dimension] = c; });

  return (
    <div className={`fixed inset-0 z-40 ${open ? '' : 'pointer-events-none'}`}>
      <div onClick={onClose}
        style={{ opacity: open ? 1 : 0, transition: animate ? 'opacity 300ms ease' : 'none' }}
        className="absolute inset-0 bg-zinc-900/20" />
      <aside
        style={{ transform: open ? 'translateX(0)' : 'translateX(100%)', transition: animate ? 'transform 300ms cubic-bezier(0.16,1,0.3,1)' : 'none' }}
        className="absolute right-0 top-0 flex h-full w-full max-w-[480px] flex-col bg-white shadow-2xl shadow-zinc-900/10">
        {a && (
          <>
            {/* Header */}
            <div className="shrink-0 border-b border-zinc-100 px-6 pt-5 pb-5">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <h2 className="text-[19px] font-semibold leading-tight text-zinc-900 text-pretty">{a.name}</h2>
                  {a.domain && (
                    <a href={`https://${a.domain}`} target="_blank" rel="noreferrer"
                      className="mt-1 inline-flex items-center gap-1 text-[13px] text-zinc-500 transition-colors hover:text-indigo-600">
                      {a.domain}<Icons.ext className="h-3.5 w-3.5" />
                    </a>
                  )}
                </div>
                <button onClick={onClose}
                  className="-mr-1.5 shrink-0 rounded-lg p-1.5 text-zinc-400 transition-colors hover:bg-zinc-100 hover:text-zinc-700">
                  <Icons.x className="h-5 w-5" />
                </button>
              </div>
              <div className="mt-3 flex flex-wrap items-center gap-2 text-[13px] text-zinc-500">
                <SegmentBadge segment={a.segment} size="lg" />
                <SourceTag source={a.source} />
                {fw && (<><span className="text-zinc-300">·</span><span className="text-[12px] text-zinc-400">{fw.label} rubric · {fw.version}</span></>)}
                <span className="inline-flex items-center gap-1.5 rounded-full bg-zinc-100 px-2 py-0.5 text-[11px] font-medium text-zinc-500 ring-1 ring-inset ring-zinc-200"><window.Icons.doc className="h-3 w-3 text-zinc-400" />Landing Page ready</span>
              </div>
            </div>

            {/* Scroll body */}
            <div className="flex-1 overflow-y-auto px-6 py-5">
              {/* Score summary */}
              <div className="flex items-center gap-5 rounded-xl border border-zinc-200 bg-zinc-50/50 px-5 py-4">
                <ScoreRing total={a.total} max={a.max_total} band={tier.band} size="lg" />
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <TierBadge band={tier.band} label={tier.label} size="lg" />
                  </div>
                  <div className="mt-2 text-[13px] text-zinc-500">
                    <span className="font-semibold text-zinc-800">{a.total}</span> of {a.max_total} points
                    <span className="text-zinc-300"> · </span>
                    {fw.dimensions.length} dimensions
                  </div>
                  <div className="mt-0.5 text-[12px] text-zinc-400">Scored {relativeTime(a.scored_at)} · Sonnet</div>
                </div>
              </div>

              {/* Pillar scores */}
              <div className="mt-7">
                <SectionLabel>Pillar scores</SectionLabel>
                <div className="grid grid-cols-3 gap-3">
                  {window.pillarsFor(a).map((p) => {
                    const ratio = p.max ? p.score / p.max : 0;
                    const st = window.BAND_STYLE[tier.band] || window.BAND_STYLE.low;
                    return (
                      <div key={p.key} className="rounded-xl border border-zinc-200 px-3.5 py-3">
                        <div className="truncate text-[10px] font-semibold uppercase tracking-wider text-zinc-400">{p.label}</div>
                        <div className="mt-1.5 flex items-baseline gap-0.5"><span className="text-[22px] font-semibold tabular-nums text-zinc-900">{p.score}</span><span className="text-[12px] text-zinc-400">/{p.max}</span></div>
                        <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-zinc-100"><div className="h-full rounded-full" style={{ width: `${ratio * 100}%`, background: st.ring, transition: 'width 0.7s cubic-bezier(0.16,1,0.3,1)' }} /></div>
                      </div>
                    );
                  })}
                </div>
              </div>

              {/* QA verdict */}
              <div className="mt-7">
                <SectionLabel>Independent QA</SectionLabel>
                {a.qa ? <QAVerdict qa={a.qa} /> : <p className="text-[13px] text-zinc-400">No QA pass yet.</p>}
              </div>

              {/* Dimension breakdown */}
              <div className="mt-7">
                <SectionLabel>Score breakdown</SectionLabel>
                <div className="rounded-xl border border-zinc-200 px-4 py-1">
                  {a.dimensions.map((d, i) => (
                    <DimensionRow key={i} dim={d} correction={corrByDim[d.label]} />
                  ))}
                </div>
              </div>

              {/* Recommendation */}
              <div className="mt-7">
                <SectionLabel>Recommendation</SectionLabel>
                <div className="rounded-xl border border-indigo-100 bg-indigo-50/40 px-4 py-3.5">
                  <div className="mb-1.5 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-indigo-500">
                    <window.Icons.compass className="h-3.5 w-3.5" />How to play it
                  </div>
                  <p className="text-[13.5px] leading-relaxed text-zinc-700 text-pretty">{a.recommendation}</p>
                </div>
              </div>

              {/* Firmographics */}
              {a.firmographics && Object.keys(a.firmographics).length > 0 && (
                <div className="mt-7">
                  <SectionLabel>Known facts {a.source === 'csv' ? '· from import' : ''}</SectionLabel>
                  <div className="rounded-xl border border-zinc-200 px-4 py-1">
                    {Object.entries(a.firmographics).map(([k, v]) => <ScoreField key={k} label={k}>{v}</ScoreField>)}
                  </div>
                </div>
              )}

              {/* Provenance */}
              {a.source === 'discovery' && (
                <div className="mt-7">
                  <SectionLabel>Origin</SectionLabel>
                  <a href={a.domain ? `https://${a.domain}` : '#'} onClick={(e) => { if (!a.domain) e.preventDefault(); }} target="_blank" rel="noreferrer"
                    className="flex items-center justify-between gap-3 rounded-xl border border-zinc-200 px-4 py-3 transition-colors hover:bg-zinc-50 hover:border-zinc-300">
                    <div className="flex items-center gap-2.5 text-[13px] text-zinc-600">
                      <window.Icons.compass className="h-4 w-4 text-zinc-400" />
                      Promoted from Discovery
                    </div>
                    <span className="inline-flex items-center gap-1 text-[12px] text-zinc-400">View discovery signals<Icons.arrowRight className="h-3.5 w-3.5" /></span>
                  </a>
                </div>
              )}
            </div>

            {/* Action bar */}
            <div className="shrink-0 border-t border-zinc-100 bg-white px-6 py-4">
              <div className="flex items-center gap-2">
                <button onClick={onOpenLanding}
                  className="inline-flex flex-1 items-center justify-center gap-1.5 rounded-lg bg-indigo-600 px-4 py-2.5 text-[13px] font-semibold text-white shadow-sm transition-colors hover:bg-indigo-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-300">
                  <window.Icons.doc className="h-4 w-4" />Open Landing Page
                </button>
                <button onClick={onAddToList}
                  className="inline-flex items-center gap-1.5 rounded-lg border border-zinc-200 bg-white px-3.5 py-2.5 text-[13px] font-medium text-zinc-600 transition-colors hover:bg-zinc-50 hover:text-zinc-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-zinc-200">
                  <window.Icons.plus className="h-4 w-4" />Add to list
                </button>
                <button onClick={onRescore} title="Re-score"
                  className="inline-flex items-center justify-center rounded-lg border border-zinc-200 bg-white p-2.5 text-zinc-500 transition-colors hover:bg-zinc-50 hover:text-zinc-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-zinc-200">
                  <Icons.refresh className="h-4 w-4" />
                </button>
              </div>
            </div>
          </>
        )}
      </aside>
    </div>
  );
}
window.ScoreDrawer = ScoreDrawer;
