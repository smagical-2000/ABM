// Real API layer — replaces mockData.js. Talks to the FastAPI backend that
// serves this page, so the base URL is just the current origin.
//
// Every PanelCompany the API returns already matches the shape the components
// expect (the design was built to the ReviewService DTOs), so there's no
// mapping layer — the JSON flows straight into the UI.

window.NOW = Date.now(); // for relativeTime() in ui.jsx

const BASE = ''; // same origin as the served page

async function http(path, opts) {
  const res = await fetch(BASE + path, {
    headers: { 'Content-Type': 'application/json' },
    ...opts,
  });
  if (!res.ok) {
    const detail = await res.text().catch(() => res.statusText);
    throw new Error(`${res.status} ${detail}`);
  }
  return res.status === 204 ? null : res.json();
}

window.API = {
  stats: () => http('/api/stats'),
  activity: () => http('/api/activity'),
  panel: ({ status = 'qualified', segment, signal_type } = {}) => {
    const q = new URLSearchParams({ status });
    if (segment && segment !== 'all') q.set('segment', segment);
    if (signal_type && signal_type !== 'all') q.set('signal_type', signal_type);
    return http(`/api/panel?${q.toString()}`);
  },
  company: (key) => http(`/api/company/${encodeURIComponent(key)}`),
  promote: (key) => http(`/api/company/${encodeURIComponent(key)}/promote`, { method: 'POST' }),
  defer: (key) => http(`/api/company/${encodeURIComponent(key)}/defer`, { method: 'POST' }),
  restore: (key) => http(`/api/company/${encodeURIComponent(key)}/restore`, { method: 'POST' }),
  reject: (key, reason) =>
    http(`/api/company/${encodeURIComponent(key)}/reject`, {
      method: 'POST',
      body: JSON.stringify({ reason }),
    }),

  // ── scoring phase ──────────────────────────────────────────────────────────
  frameworks: () => http('/api/scoring/frameworks'),
  scored: () => http('/api/scored'),
  account: (id) => http(`/api/account/${encodeURIComponent(id)}`),
  scoreAccount: (id) => http(`/api/account/${encodeURIComponent(id)}/score`, { method: 'POST' }),
  // Kick the on-demand deep-research dossier; poll account(id) until ready.
  generateDossier: (id) => http(`/api/account/${encodeURIComponent(id)}/dossier`, { method: 'POST' }),
  scoringActivity: () => http('/api/scoring/activity'),
  // Spend summary for the cost meter (month-to-date vs budget, total, avg).
  scoringStats: () => http('/api/scoring/stats'),
  // Score parked (queued) accounts in a bounded background batch. Pass
  // { limit } to score a slice, or {} for all queued.
  scoreQueued: (body = {}) => http('/api/scoring/score-queued', {
    method: 'POST', body: JSON.stringify(body),
  }),
  // Clear every score back to 'queued' (non-destructive) to re-run + re-measure.
  resetScores: () => http('/api/scoring/reset', { method: 'POST' }),
  // CSV import posts the raw file text as the body (no multipart).
  importPreview: (csvText) => http('/api/scoring/import/preview', {
    method: 'POST', headers: { 'Content-Type': 'text/csv' }, body: csvText,
  }),
  importCommit: (csvText, filename) => http('/api/scoring/import', {
    method: 'POST',
    headers: { 'Content-Type': 'text/csv', 'X-Import-Filename': filename || '' },
    body: csvText,
  }),
  // Distinct CSV import batches (label + count) for the Import filter.
  scoringImports: () => http('/api/scoring/imports'),
};
