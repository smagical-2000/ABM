// ── CompanyDrawer ───────────────────────────────────────────────────────────
function Field({ label, children }) {
  return (
    <div className="flex items-baseline justify-between gap-4 py-2.5 border-b border-zinc-100 last:border-0">
      <span className="shrink-0 text-[13px] text-zinc-400">{label}</span>
      <span className="text-right text-[13px] font-medium text-zinc-800">{children}</span>
    </div>
  );
}

function SectionLabel({ children }) {
  return (
    <div className="mb-3 flex items-center gap-2">
      <span className="text-[11px] font-semibold uppercase tracking-wider text-zinc-400">{children}</span>
      <span className="h-px flex-1 bg-zinc-100" />
    </div>
  );
}

// ── JobHiringBlock — open RCM roles grouped by role, each posting linked ─────
// Volume is the headline (count = pain intensity); each opening links out.
function JobHiringBlock({ jobs }) {
  const groups = groupRoleItems(jobs);
  return (
    <div className="mt-7">
      <SectionLabel>Open RCM roles · {jobs.length}</SectionLabel>
      <div className="space-y-2.5">
        {groups.map((g, i) => (
          <div key={i} className="rounded-xl border border-emerald-100 bg-emerald-50/40 px-3.5 py-3">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2 text-[13px] font-semibold text-emerald-800">
                <Icons.job className="h-4 w-4" />{g.role}
              </div>
              <span className="rounded-full bg-emerald-100 px-2 py-0.5 text-[11px] font-semibold tabular-nums text-emerald-700">
                {g.items.length} {g.items.length === 1 ? 'opening' : 'openings'}
              </span>
            </div>
            <ul className="mt-2 space-y-1.5">
              {g.items.map((s, j) => (
                <li key={j} className="flex items-center justify-between gap-2 text-[12px]">
                  {s.url ? (
                    <a href={s.url} target="_blank" rel="noreferrer"
                      className="min-w-0 truncate font-medium text-zinc-700 underline-offset-2 hover:text-indigo-600 hover:underline">
                      {s.title || s.summary}
                    </a>
                  ) : (
                    <span className="min-w-0 truncate text-zinc-700">{s.title || s.summary}</span>
                  )}
                  <span className="flex shrink-0 items-center gap-1.5 text-zinc-400">
                    {s.location && <span className="max-w-[110px] truncate">{s.location}</span>}
                    {s.age && <span className="whitespace-nowrap">· {s.age}</span>}
                    {s.url && (
                      <a href={s.url} target="_blank" rel="noreferrer"
                        className="shrink-0 text-zinc-400 transition-colors hover:text-indigo-600"
                        title="View job posting">
                        <Icons.ext className="h-3.5 w-3.5" />
                      </a>
                    )}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
    </div>
  );
}

function CompanyDrawer({ company, onClose, onPromote, onDefer, onReject }) {
  const open = !!company;
  return (
    <div className={`fixed inset-0 z-40 ${open ? '' : 'pointer-events-none'}`}>
      {/* Scrim */}
      <div
        onClick={onClose}
        className={`absolute inset-0 bg-zinc-900/20 transition-opacity duration-300 ${open ? 'opacity-100' : 'opacity-0'}`} />
      {/* Panel */}
      <aside
        className={`absolute right-0 top-0 flex h-full w-full max-w-[460px] flex-col bg-white shadow-2xl shadow-zinc-900/10 transition-transform duration-300 ease-out
          ${open ? 'translate-x-0' : 'translate-x-full'}`}>
        {company && (
          <>
            {/* Header */}
            <div className="shrink-0 border-b border-zinc-100 px-6 pt-5 pb-5">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <h2 className="text-[19px] font-semibold leading-tight text-zinc-900 text-pretty">{company.name}</h2>
                  {company.domain && (
                    <a href={`https://${company.domain}`} target="_blank" rel="noreferrer"
                      className="mt-1 inline-flex items-center gap-1 text-[13px] text-zinc-500 transition-colors hover:text-indigo-600">
                      {company.domain}<Icons.ext className="h-3.5 w-3.5" />
                    </a>
                  )}
                </div>
                <button onClick={onClose}
                  className="-mr-1.5 shrink-0 rounded-lg p-1.5 text-zinc-400 transition-colors hover:bg-zinc-100 hover:text-zinc-700">
                  <Icons.x className="h-5 w-5" />
                </button>
              </div>
              <div className="mt-3 flex flex-wrap items-center gap-2 text-[13px] text-zinc-500">
                <SegmentBadge segment={company.segment} size="lg" />
                {company.sub_segment && <span>{company.sub_segment.replace(/_/g, ' ')}</span>}
                {company.company_type && (
                  <>
                    <span className="text-zinc-300">·</span>
                    <span>{company.company_type}</span>
                  </>
                )}
              </div>
            </div>

            {/* Scroll body */}
            <div className="flex-1 overflow-y-auto px-6 py-5">
              {/* Confidence */}
              <div className="rounded-xl border border-zinc-200 bg-zinc-50/60 px-4 py-3.5">
                <div className="mb-2 flex items-center justify-between">
                  <span className="text-[13px] font-medium text-zinc-700">ICP confidence</span>
                  <span className="inline-flex items-center gap-1 text-[11px] text-zinc-400">
                    <Icons.sparkle className="h-3.5 w-3.5" />AI-assessed
                  </span>
                </div>
                <ConfidenceMeter value={company.confidence} size="lg" />
              </div>

              {/* Why qualified */}
              <div className="mt-7">
                <SectionLabel>{company.bucket === 'needs_review' ? 'Why flagged for review' : 'Why qualified'}</SectionLabel>
                <p className="text-[14px] leading-relaxed text-zinc-700 text-pretty">{company.reasoning}</p>
                {company.evidence_url && (
                  <a href={company.evidence_url} target="_blank" rel="noreferrer"
                    className="mt-3 inline-flex items-center gap-1.5 rounded-lg border border-zinc-200 bg-white px-3 py-1.5 text-[12px] font-medium text-zinc-600 transition-colors hover:bg-zinc-50 hover:text-indigo-600">
                    View evidence<Icons.ext className="h-3.5 w-3.5" />
                  </a>
                )}
              </div>

              {/* Firmographics */}
              <div className="mt-7">
                <SectionLabel>Firmographics</SectionLabel>
                <div className="rounded-xl border border-zinc-200 px-4 py-1">
                  <Field label="Segment">{SEGMENT_META[company.segment]?.label || '—'}</Field>
                  <Field label="Sub-segment">{company.sub_segment ? company.sub_segment.replace(/_/g, ' ') : '—'}</Field>
                  <Field label="Employees">{company.approximate_employees ? `~${company.approximate_employees.toLocaleString()}` : '—'}</Field>
                  <Field label="Domain">{company.domain || '—'}</Field>
                </div>
              </div>

              {/* Why discovered — non-job signals as a timeline; job postings
                  collapse into the role-grouped hiring block below. */}
              {(() => {
                const jobs = company.signals.filter((s) => s.signal_type === 'job_posting');
                const others = company.signals.filter((s) => s.signal_type !== 'job_posting');
                return (
                  <>
                    {others.length > 0 && (
                      <div className="mt-7">
                        <SectionLabel>Why discovered · {others.length} {others.length === 1 ? 'signal' : 'signals'}</SectionLabel>
                        <ol className="relative ml-1 space-y-4 border-l border-zinc-150 pl-5">
                          {others.map((s, i) => {
                            const m = SIGNAL_META[s.signal_type] || {};
                            const Icon = m.icon || Icons.sparkle;
                            return (
                              <li key={i} className="relative">
                                <span className={`absolute -left-[27px] top-0.5 flex h-5 w-5 items-center justify-center rounded-full ring-4 ring-white ${m.chip || 'bg-zinc-100 text-zinc-500'}`}>
                                  <Icon className="h-3 w-3" />
                                </span>
                                <div className="text-[14px] font-medium text-zinc-800">{s.summary}</div>
                                <div className="mt-0.5 flex items-center gap-1.5 text-[12px] text-zinc-400">
                                  <span className="capitalize">{(m.label || s.signal_type).toLowerCase()}</span>
                                  <span className="text-zinc-300">·</span>
                                  <span>{shortDate(s.observed_at)}</span>
                                  {typeof s.strength === 'number' && (
                                    <>
                                      <span className="text-zinc-300">·</span>
                                      <span>strength {s.strength.toFixed(2)}</span>
                                    </>
                                  )}
                                </div>
                              </li>
                            );
                          })}
                        </ol>
                      </div>
                    )}
                    {jobs.length > 0 && <JobHiringBlock jobs={jobs} />}
                  </>
                );
              })()}
            </div>

            {/* Action bar */}
            <div className="shrink-0 border-t border-zinc-100 bg-white px-6 py-4">
              <div className="flex items-center gap-2">
                <button onClick={onPromote}
                  className="inline-flex flex-1 items-center justify-center gap-1.5 rounded-lg bg-indigo-600 px-4 py-2.5 text-[13px] font-semibold text-white shadow-sm transition-colors hover:bg-indigo-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-300">
                  <Icons.arrowUp className="h-4 w-4" />Promote to scoring
                </button>
                <DeferButton onClick={onDefer} size="lg" />
                <RejectButton onClick={onReject} size="lg" />
              </div>
            </div>
          </>
        )}
      </aside>
    </div>
  );
}
window.CompanyDrawer = CompanyDrawer;
