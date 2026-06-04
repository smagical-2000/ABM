// ── CSV import wizard ────────────────────────────────────────────────────────
// Choose a file → confirm the detected schema + column mapping → review new vs
// known → import. The file text is sent to the API: /preview parses + maps
// without persisting; /import parks the new accounts as 'queued' (free) to be
// scored on demand, so importing a large file never spends money by itself.
const { useState, useRef } = React;

// Rough Sonnet cost per account when scored later: one scoring web-search call,
// no QA (Definitive facts are trusted). Only used to estimate the queue's cost.
const EST_COST_PER_ACCOUNT = 0.25;

function StepDot({ n, label, active, done }) {
  return (
    <div className="flex items-center gap-2">
      <span className={`flex h-5 w-5 items-center justify-center rounded-full text-[11px] font-semibold tabular-nums transition-colors
        ${done ? 'bg-indigo-600 text-white' : active ? 'bg-indigo-100 text-indigo-700 ring-2 ring-indigo-200' : 'bg-zinc-100 text-zinc-400'}`}>
        {done ? <Icons.check className="h-3 w-3" /> : n}
      </span>
      <span className={`text-[12.5px] font-medium ${active || done ? 'text-zinc-700' : 'text-zinc-400'}`}>{label}</span>
    </div>
  );
}

function ImportModal({ onClose, onImported, pushToast }) {
  const [step, setStep] = useState(1);       // 1 choose · 2 map · 3 review
  const [fileName, setFileName] = useState(null);
  const [csvText, setCsvText] = useState(null);
  const [preview, setPreview] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const inputRef = useRef(null);

  async function ingest(file) {
    if (!file) return;
    setError(null);
    setFileName(file.name);
    setBusy(true);
    try {
      const text = await file.text();
      setCsvText(text);
      const p = await window.API.importPreview(text);
      setPreview(p);
      setStep(2);
    } catch (e) {
      setError(humanize(e.message));
    } finally {
      setBusy(false);
    }
  }

  async function commit() {
    setBusy(true);
    setError(null);
    try {
      const res = await window.API.importCommit(csvText);
      onImported(res);
    } catch (e) {
      setError(humanize(e.message));
      setBusy(false);
    }
  }

  const onDrop = (e) => {
    e.preventDefault();
    ingest(e.dataTransfer.files && e.dataTransfer.files[0]);
  };
  const segLabel = preview && window.SEGMENT_META[preview.segment]
    ? window.SEGMENT_META[preview.segment].label : preview && preview.segment;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-zinc-900/30 backdrop-blur-[2px] animate-fade" onClick={onClose} />
      <div className="relative w-full max-w-xl rounded-2xl border border-zinc-200 bg-white shadow-xl animate-pop">
        <div className="flex items-center justify-between gap-4 border-b border-zinc-100 px-6 py-4">
          <h3 className="text-[16px] font-semibold text-zinc-900">Import accounts</h3>
          <button onClick={onClose} className="-mr-1.5 rounded-lg p-1.5 text-zinc-400 transition-colors hover:bg-zinc-100 hover:text-zinc-700">
            <Icons.x className="h-5 w-5" />
          </button>
        </div>
        <div className="flex items-center gap-3 border-b border-zinc-100 px-6 py-3">
          <StepDot n={1} label="Choose file" active={step === 1} done={step > 1} />
          <span className="h-px w-6 bg-zinc-200" />
          <StepDot n={2} label="Map columns" active={step === 2} done={step > 2} />
          <span className="h-px w-6 bg-zinc-200" />
          <StepDot n={3} label="Review" active={step === 3} done={false} />
        </div>

        <div className="px-6 py-5">
          {error && (
            <div className="mb-4 flex items-start gap-2 rounded-lg bg-rose-50 px-3 py-2.5 text-[12.5px] text-rose-700 ring-1 ring-inset ring-rose-100">
              <Icons.alert className="mt-0.5 h-4 w-4 shrink-0 text-rose-500" />{error}
            </div>
          )}

          {/* STEP 1 — choose */}
          {step === 1 && (
            <div>
              <p className="text-[13px] text-zinc-500">Drop a Definitive Healthcare export (Health Systems or Physician Groups). We auto-detect the schema and map the columns that pre-fill the rubric.</p>
              <input ref={inputRef} type="file" accept=".csv,text/csv" className="hidden"
                onChange={(e) => ingest(e.target.files && e.target.files[0])} />
              <div
                onClick={() => inputRef.current && inputRef.current.click()}
                onDragOver={(e) => e.preventDefault()} onDrop={onDrop}
                className="mt-4 flex cursor-pointer flex-col items-center justify-center rounded-xl border-2 border-dashed border-zinc-200 bg-zinc-50/50 px-6 py-9 text-center transition-colors hover:border-indigo-200 hover:bg-indigo-50/30">
                <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-white text-zinc-400 ring-1 ring-zinc-200">
                  {busy ? <Icons.refresh className="h-6 w-6 animate-spin" /> : <Icons.upload className="h-6 w-6" />}
                </div>
                <p className="mt-3 text-[13px] font-medium text-zinc-600">{busy ? 'Reading…' : fileName || 'Drag a CSV here, or click to choose'}</p>
                <p className="mt-0.5 text-[12px] text-zinc-400">.csv · domain-first dedupe</p>
              </div>
            </div>
          )}

          {/* STEP 2 — map */}
          {step === 2 && preview && (
            <div>
              <div className="flex items-center gap-2.5 rounded-lg bg-emerald-50/70 px-3 py-2.5 ring-1 ring-inset ring-emerald-100">
                <Icons.check className="h-4 w-4 shrink-0 text-emerald-500" />
                <span className="text-[12.5px] text-emerald-800"><span className="font-medium">{preview.schema_label}</span> detected · {preview.rows_total} rows · segment <span className="font-medium">{segLabel}</span></span>
              </div>
              <div className="mt-4 max-h-[260px] overflow-y-auto rounded-xl border border-zinc-200">
                <table className="w-full text-[12.5px]">
                  <thead className="sticky top-0 bg-zinc-50/95 text-[11px] uppercase tracking-wide text-zinc-400">
                    <tr><th className="px-3 py-2 text-left font-medium">CSV column</th><th className="px-3 py-2 text-left font-medium">Rubric fact</th></tr>
                  </thead>
                  <tbody>
                    {preview.mapping.map((m, i) => (
                      <tr key={i} className="border-t border-zinc-100">
                        <td className="px-3 py-2 font-medium text-zinc-700">{m.col}</td>
                        <td className="px-3 py-2">{m.fact
                          ? <span className="inline-flex items-center gap-1.5 rounded-md bg-indigo-50 px-2 py-0.5 text-[11px] font-medium text-indigo-600 ring-1 ring-inset ring-indigo-100">{m.fact}</span>
                          : <span className="text-zinc-300">account name</span>}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="mt-2.5 text-[12px] text-zinc-400">Mapped columns are sent to the scorer as known facts, so it doesn’t re-infer what you’ve already given it.</p>
            </div>
          )}

          {/* STEP 3 — review */}
          {step === 3 && preview && (
            <div>
              <div className="flex items-center gap-3">
                <div className="flex-1 rounded-xl border border-emerald-200 bg-emerald-50/50 px-3.5 py-2.5">
                  <div className="text-[20px] font-semibold tabular-nums text-emerald-700">{preview.new_count}</div>
                  <div className="text-[12px] text-emerald-600/80">new, queued to score</div>
                </div>
                <div className="flex-1 rounded-xl border border-zinc-200 bg-zinc-50/60 px-3.5 py-2.5">
                  <div className="text-[20px] font-semibold tabular-nums text-zinc-500">{preview.known_count}</div>
                  <div className="text-[12px] text-zinc-400">already known, skipped</div>
                </div>
              </div>
              <p className="mt-3 text-[12px] text-zinc-400">Importing is free. Accounts land in a <span className="font-medium text-zinc-500">queue</span>, then you score them on demand from the Scored tab (about <span className="font-medium text-zinc-500">~${Math.max(1, Math.round(preview.new_count * EST_COST_PER_ACCOUNT))}</span> total on Sonnet, no separate QA pass since the Definitive facts are authoritative).</p>
              <div className="mt-4 max-h-[220px] overflow-y-auto rounded-xl border border-zinc-200">
                <table className="w-full text-[12.5px]">
                  <thead className="sticky top-0 bg-zinc-50/95 text-[11px] uppercase tracking-wide text-zinc-400">
                    <tr><th className="px-3 py-2 text-left font-medium">Account</th><th className="px-3 py-2 text-left font-medium">Key fact</th><th className="px-3 py-2 text-left font-medium">EMR</th><th className="px-3 py-2 text-right font-medium">Status</th></tr>
                  </thead>
                  <tbody>
                    {preview.preview.map((r, i) => (
                      <tr key={i} className="border-t border-zinc-100">
                        <td className="px-3 py-2 font-medium text-zinc-700">{r.name}</td>
                        <td className="px-3 py-2 tabular-nums text-zinc-500">{r.fact || '—'}</td>
                        <td className="px-3 py-2 text-zinc-500">{r.emr || '—'}</td>
                        <td className="px-3 py-2 text-right">{r.dedupe === 'new'
                          ? <span className="text-[11px] font-medium text-emerald-600">New</span>
                          : <span className="text-[11px] font-medium text-zinc-400">Known</span>}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>

        <div className="flex items-center justify-between gap-2 border-t border-zinc-100 px-6 py-4">
          <button onClick={step === 1 ? onClose : () => setStep(step - 1)}
            className="rounded-lg px-3.5 py-2 text-[13px] font-medium text-zinc-500 transition-colors hover:bg-zinc-100 hover:text-zinc-700">
            {step === 1 ? 'Cancel' : 'Back'}
          </button>
          {step === 2 && (
            <button onClick={() => setStep(3)}
              className="rounded-lg bg-zinc-900 px-4 py-2 text-[13px] font-medium text-white transition-colors hover:bg-zinc-800">
              Looks right — review
            </button>
          )}
          {step === 3 && (
            <button onClick={commit} disabled={busy || preview.new_count === 0}
              className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-4 py-2 text-[13px] font-semibold text-white shadow-sm transition-colors hover:bg-indigo-700 disabled:cursor-not-allowed disabled:opacity-40">
              {busy ? <Icons.refresh className="h-4 w-4 animate-spin" /> : <Icons.upload className="h-4 w-4" />}
              Import {preview.new_count} to queue
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
window.ImportModal = ImportModal;

function humanize(msg) {
  if (/400/.test(msg)) return 'Unrecognized CSV. Use a Definitive Healthcare Health Systems or Physician Groups export.';
  return `Import failed: ${msg}`;
}
