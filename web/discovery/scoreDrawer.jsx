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
  const ran = qa.status !== 'skipped';
  return (
    <div className={`rounded-xl border px-4 py-3.5 ${tone.wrap}`}>
      <div className="flex items-center gap-2">
        <Icon className={`h-4 w-4 ${tone.ic}`} />
        <span className={`text-[13px] font-semibold ${tone.head}`}>{heading}</span>
        {ran && (
          <span className="ml-auto inline-flex items-center gap-1 text-[11px] text-zinc-400">
            <window.Icons.sparkle className="h-3.5 w-3.5" />Independent pass
          </span>
        )}
      </div>
      <p className={`mt-2 text-[13px] leading-relaxed ${tone.body} text-pretty`}>{qa.notes}</p>
    </div>
  );
}

// ── WarmIntrosSection — ICP decision-makers + founder warm paths ─────────────
// On demand (a small Apify spend per run, no LLM). Self-polls while generating
// so the drawer resolves live; results persist on the account (warm_intros).
// Paths are deterministic profile overlaps with evidence — never inferred.
const PATH_BADGE = {
  engaged: { label: 'Engaged with Magical', cls: 'bg-violet-50 text-violet-700 ring-violet-100' },
  shared_employer: { label: 'Shared employer', cls: 'bg-indigo-50 text-indigo-700 ring-indigo-100' },
  shared_school: { label: 'Shared school', cls: 'bg-teal-50 text-teal-700 ring-teal-100' },
};

function WarmIntrosSection({ account }) {
  const [wi, setWi] = React.useState(account.warm_intros || null);
  const [kicking, setKicking] = React.useState(false);
  React.useEffect(() => {
    setWi(account.warm_intros || null);
    setKicking(false);
  }, [account.account_id]);
  const generating = kicking || (wi && wi.state === 'generating');

  React.useEffect(() => {
    if (!generating) return;
    const id = setInterval(() => {
      window.API.account(account.account_id).then((fresh) => {
        const w = fresh && fresh.warm_intros;
        if (w && w.state !== 'generating') { setWi(w); setKicking(false); }
      }).catch(() => {});
    }, 3000);
    return () => clearInterval(id);
  }, [generating, account.account_id]);

  async function kick() {
    setKicking(true);
    try { await window.API.findWarmIntros(account.account_id); }
    catch (e) { setKicking(false); setWi({ state: 'error', error: e.message }); }
  }

  const contacts = (wi && wi.contacts) || [];
  return (
    <div className="mt-7">
      <div className="mb-3 flex items-center gap-2">
        <span className="text-[11px] font-semibold uppercase tracking-wider text-zinc-400">Warm intros</span>
        <span className="h-px flex-1 bg-zinc-100" />
        {wi && wi.state === 'ready' && (
          <button onClick={kick} title="Re-run the search"
            className="text-zinc-300 transition-colors hover:text-indigo-600">
            <Icons.refresh className="h-3.5 w-3.5" />
          </button>
        )}
      </div>

      {generating ? (
        <div className="flex items-center gap-2.5 rounded-xl border border-zinc-200 bg-zinc-50/60 px-4 py-3.5 text-[13px] text-zinc-500">
          <Icons.refresh className="h-4 w-4 animate-spin text-indigo-500" />
          Finding decision-makers and matching the founders' networks…
        </div>
      ) : wi && wi.state === 'ready' ? (
        contacts.length === 0 ? (
          <p className="text-[13px] text-zinc-400">No Director-and-above contacts surfaced — re-run later or check the account name.</p>
        ) : (
          <div className="rounded-xl border border-zinc-200">
            {contacts.map((c, i) => {
              const best = (c.paths || [])[0];
              return (
                <div key={i} className="border-b border-zinc-100 px-4 py-3 last:border-0">
                  <div className="flex items-center justify-between gap-2">
                    <div className="flex min-w-0 items-center gap-1.5">
                      {c.linkedin_url ? (
                        <a href={safeHref(c.linkedin_url)} target="_blank" rel="noreferrer"
                          className="truncate text-[13.5px] font-semibold text-zinc-800 underline-offset-2 hover:text-indigo-600 hover:underline">
                          {c.name}
                        </a>
                      ) : <span className="truncate text-[13.5px] font-semibold text-zinc-800">{c.name}</span>}
                      {c.linkedin_url && <Icons.ext className="h-3 w-3 shrink-0 text-zinc-300" />}
                    </div>
                    <div className="flex shrink-0 items-center gap-1.5">
                      {(c.paths || []).slice(0, 2).map((p, j) => {
                        const m = PATH_BADGE[p.kind] || PATH_BADGE.shared_employer;
                        return (
                          <span key={j} title={p.evidence}
                            className={`rounded-md px-1.5 py-0.5 text-[10.5px] font-semibold ring-1 ring-inset ${m.cls}`}>
                            {m.label}
                          </span>
                        );
                      })}
                    </div>
                  </div>
                  {c.title && <div className="mt-0.5 truncate text-[12.5px] text-zinc-500">{c.title}</div>}
                  {best && (
                    <div className="mt-1 text-[12px] text-zinc-400">
                      {best.founder ? `${best.founder}: ` : ''}{best.evidence}
                    </div>
                  )}
                </div>
              );
            })}
            <div className="flex items-center justify-between bg-zinc-50/60 px-4 py-2 text-[11.5px] text-zinc-400">
              <span>{wi.warm_count || 0} warm of {contacts.length} · via {(wi.founders_used || []).join(', ') || 'founders'}</span>
              {wi.generated_at && <span title={wi.generated_at}>{relativeTime(wi.generated_at)}</span>}
            </div>
          </div>
        )
      ) : (
        <div className="rounded-xl border border-zinc-200 bg-zinc-50/40 px-4 py-3.5">
          <p className="text-[13px] leading-relaxed text-zinc-500">
            Find the ICP decision-makers at {account.name} and rank them by warmth
            against the founders' networks — engagement with Magical's posts, shared
            employers, shared schools. Evidence on every path.
          </p>
          {wi && wi.state === 'error' && (
            <p className="mt-2 text-[12.5px] text-rose-600">Last run failed: {wi.error || 'unknown error'}</p>
          )}
          <button onClick={kick}
            className="mt-3 inline-flex items-center gap-1.5 rounded-lg bg-zinc-900 px-3.5 py-2 text-[12.5px] font-medium text-white transition-colors hover:bg-zinc-800">
            <Icons.leadership className="h-4 w-4" />Find warm intros
          </button>
        </div>
      )}
    </div>
  );
}

function ScoreDrawer({ account, onClose, onRescore, onOpenLanding }) {
  const open = !!account;
  const animate = typeof document === 'undefined' || document.visibilityState !== 'hidden';
  const a = account;
  // Never null: the score-summary block dereferences tier.band, and a background
  // re-score can flip an open account's total to null between renders — a null
  // tier there would throw and white-screen the whole app (no error boundary).
  const tier = (a && a.total != null && (a.tier || window.tierFor(a.framework, a.total)))
    || { band: 'low', label: '' };
  const fw = a ? window.FRAMEWORKS[a.framework] : null;
  // map corrections by dimension label for inline rendering
  const corrByDim = {};
  if (a && a.qa && a.qa.corrections) a.qa.corrections.forEach((c) => { corrByDim[c.dimension] = c; });
  // analyst (pre-QA) score per dimension, so corrected rows show original -> official
  const analystByKey = {};
  const qaApplied = !!(a && a.qa && a.qa.applied);
  if (qaApplied) (a.qa.analyst_dimensions || []).forEach((d) => { analystByKey[d.key] = d.score; });

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
                {a.import_label && (<>
                  <span className="text-zinc-300">·</span>
                  <span className="inline-flex items-center gap-1 text-[12px] text-zinc-400" title={`Imported from ${a.import_label}`}>
                    <window.Icons.doc className="h-3.5 w-3.5 text-zinc-400" />{a.import_label.split(' · ')[0]}
                  </span>
                </>)}
                {fw && (<><span className="text-zinc-300">·</span><span className="text-[12px] text-zinc-400">{fw.label} rubric · {fw.version}</span></>)}
                <span className="inline-flex items-center gap-1.5 rounded-full bg-zinc-100 px-2 py-0.5 text-[11px] font-medium text-zinc-500 ring-1 ring-inset ring-zinc-200"><window.Icons.doc className="h-3 w-3 text-zinc-400" />Landing Page ready</span>
              </div>
              <AbmCallout match={a.abm_match} />
              <DiscoverySignals signals={a.discovery_signals} />
            </div>

            {/* Scroll body */}
            <div className="flex-1 overflow-y-auto px-6 py-5">
              {/* Score summary */}
              <div className="flex items-center gap-5 rounded-xl border border-zinc-200 bg-zinc-50/50 px-5 py-4">
                <ScoreRing total={a.total} max={a.max_total} band={tier.band} size="lg" />
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <TierBadge band={tier.band} label={window.fitWord(tier.band)} size="lg" />
                    {a.segment === 'health_system' && tier.label && (
                      <span className="text-[12px] font-medium text-zinc-400">{tier.label}</span>
                    )}
                    {qaApplied && (
                      <span className="inline-flex items-center gap-1 rounded-full bg-amber-50 px-2 py-0.5 text-[11px] font-medium text-amber-700 ring-1 ring-inset ring-amber-100" title="Independent QA corrected the analyst's score">
                        <window.Icons.shieldCheck className="h-3 w-3 text-amber-500" />Adjusted by QA
                      </span>
                    )}
                  </div>
                  <div className="mt-2 text-[13px] text-zinc-500">
                    {qaApplied && a.qa.analyst_total != null && (
                      <span className="mr-1 text-zinc-400 line-through">{a.qa.analyst_total}</span>
                    )}
                    <span className="font-semibold text-zinc-800">{a.total}</span> of {a.max_total} points
                    <span className="text-zinc-300"> · </span>
                    {/* Fall back to the account's own dimensions if the framework
                        config isn't loaded / is an unknown key — never crash the
                        drawer (which white-screens the whole app). */}
                    {(fw ? fw.dimensions : (a.dimensions || [])).length} dimensions
                  </div>
                  <div className="mt-0.5 text-[12px] text-zinc-400" title={a.scored_at || ''}>Scored {formatDateTime(a.scored_at)} · Sonnet{qaApplied ? ' · QA-corrected' : ''}</div>
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
                    <DimensionRow key={i} dim={d} correction={corrByDim[d.key] || corrByDim[d.label]}
                      analystScore={analystByKey[d.key]} />
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

              {/* Warm intros — decision-makers + founder paths (scored only) */}
              {a.state === 'scored' && <WarmIntrosSection account={a} />}

              {/* Known facts — carried into the scorer so it does not re-research */}
              {a.firmographics && Object.keys(a.firmographics).length > 0 && (
                <div className="mt-7">
                  <SectionLabel>Known facts · {a.source === 'csv' ? 'from import' : 'from discovery'}</SectionLabel>
                  <div className="rounded-xl border border-zinc-200 px-4 py-1">
                    {Object.entries(a.firmographics).map(([k, v]) => (
                      String(v).length > 48 ? (
                        <div key={k} className="border-b border-zinc-100 py-2.5 last:border-0">
                          <div className="text-[12px] text-zinc-400">{k}</div>
                          <div className="mt-1 text-[13px] leading-relaxed text-zinc-700 text-pretty">{v}</div>
                        </div>
                      ) : <ScoreField key={k} label={k}>{v}</ScoreField>
                    ))}
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
                <button onClick={onRescore} title="Re-score this account"
                  className="inline-flex items-center justify-center gap-1.5 rounded-lg border border-zinc-200 bg-white px-3.5 py-2.5 text-[13px] font-medium text-zinc-500 transition-colors hover:bg-zinc-50 hover:text-zinc-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-zinc-200">
                  <Icons.refresh className="h-4 w-4" />Re-score
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
