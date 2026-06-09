// ── Landing Page (account dossier) ───────────────────────────────────────────
// A board-ready document rendered exactly as it prints. The fit scores come
// from the score we already hold; the deep-research sections (firmographics,
// services, intent signals, decision makers, entry strategy, RCM complexity,
// news, pain points, messaging) are generated on demand and cost money, so the
// document shows a Generate call-to-action until the user asks for it. Download
// PDF is the browser printing only this document (see the @media print rules).

const EST_DOSSIER_COST = 0.7; // shown before generating; the meter records the real spend

// "likely"/"unknown" confidence markers, mirroring the source document's honesty.
function FactValue({ row }) {
  const c = row.confidence || 'known';
  return (
    <>
      {c === 'likely' && <span className="italic text-zinc-400">Likely </span>}
      {row.value || (c === 'unknown' ? <span className="italic text-zinc-400">Unknown</span> : '—')}
      {c === 'unknown' && row.value ? <span className="italic text-zinc-400"> · unconfirmed</span> : null}
    </>
  );
}

function DocSection({ title, children }) {
  return (
    <section className="mt-8 break-inside-avoid">
      <h2 className="border-b border-zinc-200 pb-1.5 text-[16px] font-semibold tracking-tight text-zinc-900">{title}</h2>
      <div className="mt-3">{children}</div>
    </section>
  );
}

function KVTable({ rows }) {
  if (!rows || !rows.length) return null;
  return (
    <div className="overflow-hidden rounded-lg border border-zinc-200">
      <table className="w-full text-[13px]">
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} className="border-b border-zinc-100 align-top last:border-0">
              <td className="w-[32%] bg-zinc-50/70 px-3.5 py-2.5 font-semibold text-zinc-700">{r.label}</td>
              <td className="px-3.5 py-2.5 leading-relaxed text-zinc-600"><FactValue row={r} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function fitLine(band) {
  return band === 'out' ? 'Not a fit' : `${window.fitWord(band)} Fit`;
}

function FitScores({ pillars, total, max, band }) {
  return (
    <>
      <div className="grid grid-cols-3 gap-px overflow-hidden rounded-lg border border-zinc-200 bg-zinc-200">
        {pillars.map((p) => (
          <div key={p.key} className="bg-white px-4 py-3">
            <div className="text-[11px] font-medium uppercase tracking-wide text-zinc-400">{p.label}</div>
            <div className="mt-1 text-[18px] font-semibold tabular-nums text-zinc-900">{p.score}<span className="text-[12px] font-normal text-zinc-400">/{p.max}</span></div>
          </div>
        ))}
      </div>
      <p className="mt-2.5 text-[13.5px] font-semibold text-zinc-800">Overall score: {total}/{max} · {fitLine(band)}</p>
    </>
  );
}

function SignalsTable({ rows }) {
  if (!rows || !rows.length) return null;
  return (
    <div className="overflow-hidden rounded-lg border border-zinc-200">
      <table className="w-full text-[13px]">
        <thead className="bg-zinc-50/70 text-[11px] uppercase tracking-wide text-zinc-400">
          <tr><th className="px-3.5 py-2 text-left font-medium">Signal</th><th className="w-16 px-3.5 py-2 text-right font-medium">Score</th></tr>
        </thead>
        <tbody>
          {rows.map((s, i) => (
            <tr key={i} className="border-t border-zinc-100 align-top">
              <td className="px-3.5 py-2.5 leading-relaxed text-zinc-600"><span className="font-semibold text-zinc-800">{s.signal}</span>{s.detail ? ` — ${s.detail}` : ''}</td>
              <td className="px-3.5 py-2.5 text-right font-semibold tabular-nums text-zinc-700">{s.score}/10</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function PeopleTable({ rows }) {
  if (!rows || !rows.length) {
    return <p className="text-[13px] text-zinc-400">No decision makers identified from CRM data for this domain.</p>;
  }
  return (
    <div className="overflow-hidden rounded-lg border border-zinc-200">
      <table className="w-full text-[13px]">
        <thead className="bg-zinc-50/70 text-[11px] uppercase tracking-wide text-zinc-400">
          <tr><th className="px-3.5 py-2 text-left font-medium">Role</th><th className="px-3.5 py-2 text-left font-medium">Contact</th><th className="px-3.5 py-2 text-left font-medium">Notes</th></tr>
        </thead>
        <tbody>
          {rows.map((p, i) => (
            <tr key={i} className="border-t border-zinc-100 align-top">
              <td className="px-3.5 py-2.5 font-semibold text-zinc-700">{p.role}</td>
              <td className="px-3.5 py-2.5 text-zinc-600">
                {p.contact}
                {p.linkedin && (
                  <a href={p.linkedin} target="_blank" rel="noreferrer" className="no-print ml-1.5 text-[11px] font-medium text-indigo-500 hover:text-indigo-700">in</a>
                )}
              </td>
              <td className="px-3.5 py-2.5 leading-relaxed text-zinc-500">{p.notes}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function EntryStrategyBlock({ es }) {
  if (!es) return null;
  return (
    <div className="space-y-3 text-[13px] leading-relaxed text-zinc-600">
      {es.timing && <p><span className="font-semibold text-zinc-800">Timing:</span> {es.timing}</p>}
      {es.primary_angles && es.primary_angles.length > 0 && (
        <div>
          <div className="font-semibold text-zinc-800">Primary angles</div>
          <ol className="mt-1 list-decimal space-y-1 pl-5">{es.primary_angles.map((x, i) => <li key={i}>{x}</li>)}</ol>
        </div>
      )}
      {es.cautions && es.cautions.length > 0 && (
        <div>
          <div className="font-semibold text-zinc-800">Caution</div>
          <ul className="mt-1 list-disc space-y-1 pl-5">{es.cautions.map((x, i) => <li key={i}>{x}</li>)}</ul>
        </div>
      )}
      {es.deal_size && <p><span className="font-semibold text-zinc-800">Deal size:</span> {es.deal_size}</p>}
    </div>
  );
}

function BulletList({ items, bold }) {
  if (!items || !items.length) return null;
  return (
    <ul className="list-disc space-y-1.5 pl-5 text-[13px] leading-relaxed text-zinc-600">
      {items.map((it, i) => (
        <li key={i}>{bold ? <><span className="font-semibold text-zinc-800">{it.headline}{it.date ? ` (${it.date})` : ''}:</span> {it.detail}</> : it}</li>
      ))}
    </ul>
  );
}

function MessagingList({ items }) {
  if (!items || !items.length) return null;
  return (
    <div className="space-y-2.5">
      {items.map((m, i) => (
        <blockquote key={i} className="break-inside-avoid border-l-2 border-zinc-300 bg-zinc-50/70 px-4 py-2.5 text-[13px] italic leading-relaxed text-zinc-600">"{m}"</blockquote>
      ))}
    </div>
  );
}

function DossierBody({ d }) {
  return (
    <>
      {d.firmographic_profile && d.firmographic_profile.length > 0 && (
        <DocSection title="Firmographic Profile"><KVTable rows={d.firmographic_profile} /></DocSection>)}
      {d.services && d.services.length > 0 && (
        <DocSection title="Services"><KVTable rows={d.services} /></DocSection>)}
      {d.intent_signals && d.intent_signals.length > 0 && (
        <DocSection title="Business Intent Signals"><SignalsTable rows={d.intent_signals} /></DocSection>)}
      <DocSection title="Decision Makers"><PeopleTable rows={d.decision_makers} /></DocSection>
      {d.entry_strategy && (d.entry_strategy.timing || (d.entry_strategy.primary_angles || []).length) && (
        <DocSection title="Entry Strategy"><EntryStrategyBlock es={d.entry_strategy} /></DocSection>)}
      {d.rcm_complexity && d.rcm_complexity.length > 0 && (
        <DocSection title="RCM Complexity"><KVTable rows={d.rcm_complexity} /></DocSection>)}
      {d.recent_news && d.recent_news.length > 0 && (
        <DocSection title="Recent News & Context"><BulletList items={d.recent_news} bold /></DocSection>)}
      {d.pain_points && d.pain_points.length > 0 && (
        <DocSection title="Key Pain Points"><BulletList items={d.pain_points} /></DocSection>)}
      {d.messaging_angles && d.messaging_angles.length > 0 && (
        <DocSection title="Messaging Angles"><MessagingList items={d.messaging_angles} /></DocSection>)}
    </>
  );
}

function GenerateCTA({ fitHigh, onGenerate, kicking, error }) {
  return (
    <div className="no-print mt-8 rounded-xl border border-indigo-100 bg-indigo-50/40 px-5 py-6 text-center">
      {error && (
        <div className="mb-3 inline-flex items-center gap-1.5 rounded-lg bg-rose-50 px-3 py-1.5 text-[12.5px] text-rose-700 ring-1 ring-inset ring-rose-100">
          <Icons.alert className="h-4 w-4 text-rose-500" />Generation failed. {error}
        </div>
      )}
      <div className="text-[15px] font-semibold text-zinc-900">Generate the full research dossier</div>
      <p className="mx-auto mt-1.5 max-w-md text-[13px] leading-relaxed text-zinc-500">
        Decision makers (via Apollo), recent news, entry strategy, RCM complexity, and ready-to-send messaging angles.
        Roughly <span className="font-medium text-zinc-600">${EST_DOSSIER_COST.toFixed(2)}</span> on Sonnet, one time, then stored.
      </p>
      <button onClick={onGenerate} disabled={kicking}
        className="mt-4 inline-flex items-center gap-2 rounded-lg bg-indigo-600 px-4 py-2.5 text-[13px] font-semibold text-white shadow-sm transition-colors hover:bg-indigo-700 disabled:cursor-not-allowed disabled:opacity-50">
        {kicking ? <Icons.refresh className="h-4 w-4 animate-spin" /> : <Icons.sparkle className="h-4 w-4" />}
        {kicking ? 'Starting…' : error ? `Retry (~$${EST_DOSSIER_COST.toFixed(2)})` : `Generate dossier (~$${EST_DOSSIER_COST.toFixed(2)})`}
      </button>
      {!fitHigh && (
        <p className="mt-3 text-[12px] text-zinc-400">Dossiers pay off most on High-fit accounts. This one isn't High fit — generate only if you're pursuing it.</p>
      )}
    </div>
  );
}

function GeneratingPanel({ name }) {
  const [secs, setSecs] = React.useState(0);
  React.useEffect(() => {
    const id = setInterval(() => setSecs((s) => s + 1), 1000);
    return () => clearInterval(id);
  }, []);
  return (
    <div className="no-print mt-8 flex items-center gap-3 rounded-xl border border-indigo-100 bg-indigo-50/40 px-5 py-5">
      <Icons.refresh className="h-5 w-5 shrink-0 animate-spin text-indigo-500" />
      <div>
        <div className="text-[13.5px] font-semibold text-zinc-800">Researching {name}…</div>
        <div className="text-[12.5px] text-zinc-500">Pulling decision makers, recent news, and the entry strategy. About a minute. <span className="tabular-nums text-zinc-400">{secs}s</span></div>
      </div>
    </div>
  );
}

function LandingPageModal({ account, onClose, pushToast }) {
  const [acc, setAcc] = React.useState(account);
  const [kicking, setKicking] = React.useState(false);
  // Tag <body> only while actually open (this component stays mounted with a
  // null account when closed), so the print stylesheet hides the app and lets
  // the document paginate (see @media print in index.html).
  React.useEffect(() => {
    if (!account) return undefined;
    document.body.classList.add('doc-open');
    return () => document.body.classList.remove('doc-open');
  }, [account && account.account_id]);
  // On open, sync to the passed account and pull the latest stored state, so a
  // dossier generated earlier shows even if the parent list hasn't refreshed.
  React.useEffect(() => {
    setAcc(account);
    if (account && account.account_id) {
      window.API.account(account.account_id).then((fresh) => { if (fresh) setAcc(fresh); }).catch(() => {});
    }
  }, [account && account.account_id]);

  const a = acc;
  const generating = a && a.dossier_state === 'generating';

  // Poll while a dossier is generating so it resolves live.
  React.useEffect(() => {
    if (!a || !generating) return undefined;
    let alive = true;
    const id = setInterval(async () => {
      try { const fresh = await window.API.account(a.account_id); if (alive && fresh) setAcc(fresh); }
      catch (_) { /* ignore */ }
    }, 3000);
    return () => { alive = false; clearInterval(id); };
  }, [generating, a && a.account_id]);

  if (!a) return null;
  const tier = a.tier || (a.total != null ? window.tierFor(a.framework, a.total) : null);
  if (!tier) return null;
  const pillars = window.pillarsFor(a);
  const dossier = a.dossier;
  const fitHigh = tier.band === 'high';
  const dateStr = (dossier && dossier.generated_at) ? window.shortDate(dossier.generated_at)
    : (a.scored_at ? window.shortDate(a.scored_at) : '');

  async function handleGenerate() {
    setKicking(true);
    try {
      const res = await window.API.generateDossier(a.account_id);
      setAcc(res);
      pushToast(`Researching ${a.name}. This takes about a minute.`, 'success');
    } catch (e) { pushToast(`Couldn't start: ${e.message}`, 'danger'); }
    setKicking(false);
  }

  const overlay = (
    <div className="landing-overlay fixed inset-0 z-[60] flex items-start justify-center overflow-y-auto p-4 sm:p-8">
      <div className="no-print absolute inset-0 bg-zinc-900/40 backdrop-blur-[3px] animate-fade" onClick={onClose} />

      <div className="landing-shell relative w-full max-w-3xl shrink-0 animate-pop">
        {/* toolbar */}
        <div className="no-print mb-3 flex items-center justify-between">
          <span className="inline-flex items-center gap-1.5 rounded-full bg-white/90 px-2.5 py-1 text-[11px] font-medium text-zinc-500 ring-1 ring-inset ring-zinc-200 backdrop-blur">
            <window.Icons.doc className="h-3.5 w-3.5 text-zinc-400" />Landing Page · {dossier ? 'research dossier' : 'preview'}
          </span>
          <div className="flex items-center gap-2">
            {dossier && (
              <button onClick={() => window.print()}
                className="inline-flex items-center gap-1.5 rounded-lg bg-white px-3 py-1.5 text-[12px] font-medium text-zinc-600 ring-1 ring-inset ring-zinc-200 transition-colors hover:bg-zinc-50 hover:text-indigo-700">
                <window.Icons.download className="h-3.5 w-3.5" />Download PDF
              </button>
            )}
            <button onClick={onClose}
              className="inline-flex items-center justify-center rounded-lg bg-white p-1.5 text-zinc-400 ring-1 ring-inset ring-zinc-200 transition-colors hover:bg-zinc-50 hover:text-zinc-700">
              <Icons.x className="h-4 w-4" />
            </button>
          </div>
        </div>

        {/* the document (this is what prints) */}
        <article className="print-document overflow-hidden rounded-2xl bg-white shadow-2xl shadow-zinc-900/15 ring-1 ring-zinc-200">
          <div className="px-9 py-9 sm:px-10">
            <div className="flex items-start justify-between gap-6">
              <div className="min-w-0">
                <h1 className="text-[26px] font-semibold leading-tight tracking-tight text-zinc-900 text-pretty">{a.name}</h1>
                {a.domain && <div className="mt-1 text-[13px] text-zinc-400">{a.domain}</div>}
                <div className="mt-2.5 flex flex-wrap items-center gap-2">
                  <SegmentBadge segment={a.segment} />
                  {a.segment === 'health_system' && tier.label && (
                    <span className="text-[12px] font-medium text-zinc-400">{tier.label}</span>
                  )}
                </div>
              </div>
              <div className="flex shrink-0 flex-col items-center">
                <ScoreRing total={a.total} max={a.max_total} band={tier.band} size="lg" />
                <div className="mt-1.5 text-[10.5px] uppercase tracking-wider text-zinc-400">Fit score</div>
              </div>
            </div>

            <DocSection title="Fit Scores">
              <FitScores pillars={pillars} total={a.total} max={a.max_total} band={tier.band} />
            </DocSection>

            {a.recommendation && (
              <DocSection title="Recommendation">
                <p className="text-[13.5px] leading-relaxed text-zinc-600 text-pretty">{a.recommendation}</p>
              </DocSection>
            )}

            {!dossier && !generating && (
              <GenerateCTA fitHigh={fitHigh} onGenerate={handleGenerate} kicking={kicking}
                error={a.dossier_state === 'error' ? a.dossier_error : null} />
            )}
            {generating && <GeneratingPanel name={a.name} />}
            {dossier && <DossierBody d={dossier} />}
          </div>

          <div className="flex items-center justify-between gap-3 border-t border-zinc-100 bg-zinc-50/60 px-9 py-3.5 text-[11px] text-zinc-400 sm:px-10">
            <span>Magical · {dossier ? 'Sonnet + Apollo' : 'fit score'}{dateStr ? ` · ${dateStr}` : ''}</span>
            <span className="inline-flex items-center gap-1.5">
              <window.Icons.compass className="h-3.5 w-3.5" />{a.source === 'csv' ? 'CSV import' : 'Discovery'}
            </span>
          </div>
        </article>
      </div>
    </div>
  );
  return ReactDOM.createPortal(overlay, document.body);
}
window.LandingPageModal = LandingPageModal;
