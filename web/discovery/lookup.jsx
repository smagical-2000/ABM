const { useState } = React;

// ── AE one-off lookup — "Research an account" bar on the Scored view ─────────
// Two-step by design (accuracy first): step 1 resolves WHO the typed company is
// (existing account / live Discovery company / web-resolved identity via Exa +
// Claude) for ~a cent; the AE confirms the identity card; only then does step 2
// spend the full research + independent-QA pass. Ambiguity is never auto-picked.
// Board-facing: no emoji.

const LOOKUP_SEGMENTS = [
  ['health_system', 'Health System'],
  ['specialty', 'Specialty Group'],
  ['payer', 'Payer'],
];

function ConfidenceChip({ level }) {
  const style = level === 'high'
    ? 'bg-emerald-50 text-emerald-700 border-emerald-200'
    : 'bg-amber-50 text-amber-700 border-amber-200';
  return (
    <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-medium ${style}`}>
      {level === 'high' ? 'High confidence' : 'Medium confidence'}
    </span>
  );
}

function EngagementChip({ engagement }) {
  if (!engagement || !engagement.tier) return null;
  return (
    <span className="inline-flex items-center gap-1 rounded-full border border-violet-200 bg-violet-50 px-2 py-0.5 text-[11px] font-medium text-violet-700"
      title="This company is already engaging with us (email / LinkedIn / podcast / SFDC)">
      <window.Icons.zap className="h-3 w-3" />Engaging — {engagement.tier}{engagement.heat != null ? ` · heat ${engagement.heat}` : ''}
    </span>
  );
}

// The one paid-action button (both the confirm card and the manual fallback):
// same title/cost hint, same disabled treatment — only the shade differs.
function CommitButton({ onClick, disabled, dark, children }) {
  return (
    <button onClick={onClick} disabled={disabled}
      title="Runs the full research pass on the segment rubric + an independent QA verification (~$0.35)"
      className={`inline-flex items-center gap-1.5 rounded-lg px-3.5 py-2 text-[13px] font-medium text-white transition-colors disabled:opacity-50 ${dark ? 'bg-zinc-900 hover:bg-zinc-800' : 'bg-indigo-600 shadow-sm hover:bg-indigo-700'}`}>
      {children}
    </button>
  );
}

function SegmentSelect({ value, onChange, disabled }) {
  return (
    <select value={value || ''} onChange={(e) => onChange(e.target.value)} disabled={disabled}
      className="rounded-lg border border-zinc-200 bg-white px-2.5 py-1.5 text-[12.5px] font-medium text-zinc-700 focus:border-indigo-400 focus:outline-none disabled:opacity-50">
      <option value="" disabled>Segment…</option>
      {LOOKUP_SEGMENTS.map(([k, label]) => <option key={k} value={k}>{label}</option>)}
    </select>
  );
}

function LookupBar({ pushToast, onOpenAccount, onStarted }) {
  const [name, setName] = useState('');
  const [website, setWebsite] = useState('');
  const [busy, setBusy] = useState(false);        // resolve in flight
  const [committing, setCommitting] = useState(false);
  const [result, setResult] = useState(null);     // resolve response
  const [pick, setPick] = useState(0);            // 0 = resolved, 1.. = alternates
  const [segment, setSegment] = useState('');
  const [manualDomain, setManualDomain] = useState('');

  function reset() {
    setResult(null); setPick(0); setSegment(''); setManualDomain(''); setCommitting(false);
  }

  async function handleResolve() {
    if (!name.trim() || busy) return;
    setBusy(true); reset();
    try {
      const r = await window.API.lookup(name.trim(), website.trim() || null);
      setResult(r);
      const seg = r.resolved && r.resolved.segment;
      setSegment(LOOKUP_SEGMENTS.some(([k]) => k === seg) ? seg : '');
      setManualDomain((r.resolved && r.resolved.domain) || website.trim());
    } catch (e) { pushToast(`Lookup failed: ${e.message}`, 'danger'); }
    finally { setBusy(false); }
  }

  // The candidate the AE currently has selected (resolved vs an alternate).
  function chosen() {
    const r = result || {};
    if (pick === 0) return r.resolved || {};
    return (r.alternates || [])[pick - 1] || {};
  }

  async function handleCommit(overrideDomain) {
    const c = chosen();
    const domain = (overrideDomain || c.domain || manualDomain || '').trim();
    if (!domain || !segment || committing) return;
    setCommitting(true);
    try {
      // Alternates carry only name/domain/description, so the resolved-only
      // fields fall out naturally as null — no pick-dependent branching.
      const res = await window.API.lookupScore({
        name: c.name || name.trim(),
        domain,
        segment,
        sub_segment: c.sub_segment || null,
        description: c.description || '',
        hq: c.hq || null,
        evidence_url: c.evidence_url || null,
        approximate_employees: c.approximate_employees != null ? c.approximate_employees : null,
      });
      if (res.status === 'already_scored') {
        pushToast('Already on the board — opening it.', 'success');
        onOpenAccount(res.account_id);
      } else if (res.status === 'in_discovery') {
        await handlePromote(res.company_key);
        return;
      } else if (res.status === 'queued' || res.budget_blocked) {
        pushToast('Monthly budget reached — account parked as queued (nothing spent).', 'danger');
        onStarted();
      } else {
        pushToast(`Deep research started for ${res.account && res.account.name ? res.account.name : name}…`, 'success');
        onStarted();
      }
      setName(''); setWebsite(''); reset();
    } catch (e) { pushToast(`Couldn't start: ${e.message}`, 'danger'); setCommitting(false); }
  }

  async function handlePromote(key) {
    setCommitting(true);
    try {
      const res = await window.API.promote(key);
      if (res.budget_blocked) pushToast('Promoted, but the monthly budget is reached — parked as queued.', 'danger');
      else pushToast('Promoted from Discovery — scoring with its live signals…', 'success');
      onStarted();
      setName(''); setWebsite(''); reset();
    } catch (e) { pushToast(`Couldn't promote: ${e.message}`, 'danger'); setCommitting(false); }
  }

  const r = result || {};
  const canCommit = !!segment && !!(chosen().domain || manualDomain.trim()) && !committing;

  return (
    <div className="mt-6 rounded-2xl border border-zinc-200 bg-white shadow-sm shadow-zinc-900/[0.02]">
      {/* input row */}
      <div className="flex flex-wrap items-center gap-3 px-5 py-4">
        <window.Icons.search className="h-4 w-4 shrink-0 text-zinc-400" />
        <input value={name} onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') handleResolve(); }}
          placeholder="Research an account — company name"
          className="min-w-[220px] flex-1 rounded-lg border border-zinc-200 px-3 py-2 text-[13.5px] text-zinc-800 placeholder:text-zinc-400 focus:border-indigo-400 focus:outline-none" />
        <input value={website} onChange={(e) => setWebsite(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') handleResolve(); }}
          placeholder="Website (optional, sharpens the match)"
          className="min-w-[200px] flex-1 rounded-lg border border-zinc-200 px-3 py-2 text-[13.5px] text-zinc-800 placeholder:text-zinc-400 focus:border-indigo-400 focus:outline-none" />
        <button onClick={handleResolve} disabled={!name.trim() || busy}
          className="inline-flex items-center gap-2 rounded-lg bg-indigo-600 px-3.5 py-2 text-[13px] font-medium text-white shadow-sm transition-colors hover:bg-indigo-700 disabled:opacity-50">
          {busy ? <><window.Icons.refresh className="h-4 w-4 animate-spin" />Resolving…</> : <>Research</>}
        </button>
        {result && (
          <button onClick={() => { reset(); }} title="Clear"
            className="rounded-lg p-2 text-zinc-400 transition-colors hover:bg-zinc-100 hover:text-zinc-600">
            <window.Icons.x className="h-4 w-4" />
          </button>
        )}
      </div>

      {/* resolve outcome */}
      {result && (
        <div className="border-t border-zinc-100 px-5 py-4">
          {r.status === 'already_scored' && (
            <div className="flex flex-wrap items-center gap-3">
              <window.Icons.check className="h-4 w-4 text-emerald-600" />
              <span className="text-[13.5px] text-zinc-700">
                Already on the board — <span className="font-semibold">{r.account.name}</span>
                {r.account.state === 'scored' && r.account.total != null
                  ? <> · {r.account.tier_label || ''} {r.account.total}/{r.account.max_total}</>
                  : <> · {r.account.state}</>}
              </span>
              <EngagementChip engagement={r.engagement} />
              <button onClick={() => { onOpenAccount(r.account_id); reset(); }}
                className="ml-auto inline-flex items-center gap-1.5 rounded-lg bg-zinc-900 px-3 py-1.5 text-[12.5px] font-medium text-white transition-colors hover:bg-zinc-800">
                Open account<window.Icons.arrowRight className="h-3.5 w-3.5" />
              </button>
            </div>
          )}

          {r.status === 'in_discovery' && (
            <div className="flex flex-wrap items-center gap-3">
              <window.Icons.sparkle className="h-4 w-4 text-indigo-500" />
              <span className="text-[13.5px] text-zinc-700">
                <span className="font-semibold">{r.company.name}</span> is live in Discovery
                with {r.company.signals} intent {r.company.signals === 1 ? 'signal' : 'signals'} —
                promoting carries {r.company.signals === 1 ? 'it' : 'them'} into the score.
              </span>
              <EngagementChip engagement={r.engagement} />
              <button onClick={() => handlePromote(r.company.key)} disabled={committing}
                className="ml-auto inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-3 py-1.5 text-[12.5px] font-medium text-white transition-colors hover:bg-indigo-700 disabled:opacity-50">
                {committing ? 'Starting…' : 'Promote & Score'}
              </button>
            </div>
          )}

          {(r.status === 'new' || r.status === 'ambiguous') && (
            <div>
              {r.status === 'ambiguous' && (
                <p className="mb-3 flex items-center gap-1.5 text-[12.5px] text-amber-700">
                  <window.Icons.info className="h-3.5 w-3.5" />
                  The web identity differs from the website you gave — pick the right company before scoring.
                </p>
              )}
              <div className="space-y-2">
                {[r.resolved, ...(r.alternates || [])].filter(Boolean).map((c, i) => (
                  <label key={i} className={`flex cursor-pointer items-start gap-3 rounded-xl border p-3 transition-colors ${pick === i ? 'border-indigo-300 bg-indigo-50/40' : 'border-zinc-200 hover:bg-zinc-50'}`}>
                    <input type="radio" name="lookup-pick" checked={pick === i} onChange={() => setPick(i)}
                      className="mt-1 h-3.5 w-3.5 accent-indigo-600" />
                    <span className="min-w-0 flex-1">
                      <span className="flex flex-wrap items-center gap-2">
                        <span className="text-[13.5px] font-semibold text-zinc-900">{c.name}</span>
                        {c.domain && <span className="rounded bg-zinc-100 px-1.5 py-0.5 font-mono text-[11px] text-zinc-500">{c.domain}</span>}
                        {i === 0 && c.confidence && <ConfidenceChip level={c.confidence} />}
                        {i === 0 && <EngagementChip engagement={r.engagement} />}
                      </span>
                      {c.description && <span className="mt-1 block text-[12.5px] leading-relaxed text-zinc-500">{c.description}</span>}
                      <span className="mt-1 flex flex-wrap items-center gap-3 text-[11.5px] text-zinc-400">
                        {i === 0 && c.hq && <span>{c.hq}</span>}
                        {i === 0 && c.evidence_url && (
                          <a href={c.evidence_url} target="_blank" rel="noreferrer"
                            className="inline-flex items-center gap-1 text-indigo-500 hover:text-indigo-600">
                            evidence<window.Icons.ext className="h-3 w-3" />
                          </a>
                        )}
                      </span>
                    </span>
                  </label>
                ))}
              </div>
              <div className="mt-3 flex flex-wrap items-center justify-end gap-2">
                <SegmentSelect value={segment} onChange={setSegment} disabled={committing} />
                <CommitButton onClick={() => handleCommit()} disabled={!canCommit}>
                  {committing ? 'Starting…' : 'Research & Score'}
                </CommitButton>
              </div>
            </div>
          )}

          {(r.status === 'non_icp' || r.status === 'unresolved') && (
            <div>
              <p className="flex items-start gap-1.5 text-[12.5px] text-zinc-600">
                <window.Icons.info className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-500" />
                <span>
                  {r.status === 'non_icp'
                    ? <>This doesn't look like a US healthcare provider or payer{r.resolved && r.resolved.reason ? <> — {r.resolved.reason}</> : ''}. Score it anyway only if you know better.</>
                    : <>Couldn't confidently identify this company{r.error ? <> ({r.error})</> : ''}. Confirm the details and score it explicitly.</>}
                </span>
              </p>
              {r.resolved && r.resolved.description && (
                <p className="mt-2 text-[12.5px] text-zinc-500">{r.resolved.description}</p>
              )}
              <div className="mt-3 flex flex-wrap items-center justify-end gap-2">
                <input value={manualDomain} onChange={(e) => setManualDomain(e.target.value)}
                  placeholder="company domain, e.g. acme.com"
                  className="w-56 rounded-lg border border-zinc-200 px-2.5 py-1.5 font-mono text-[12px] text-zinc-700 placeholder:font-sans placeholder:text-zinc-400 focus:border-indigo-400 focus:outline-none" />
                <SegmentSelect value={segment} onChange={setSegment} disabled={committing} />
                <CommitButton dark onClick={() => handleCommit(manualDomain)} disabled={!canCommit}>
                  {committing ? 'Starting…' : 'Score anyway'}
                </CommitButton>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
window.LookupBar = LookupBar;
