// Real API layer — replaces mockData.js. Talks to the FastAPI backend that
// serves this page, so the base URL is just the current origin.
//
// Every PanelCompany the API returns already matches the shape the components
// expect (the design was built to the ReviewService DTOs), so there's no
// mapping layer — the JSON flows straight into the UI.

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
  // Manually pull the last 24h of signals into the panel (browserless sources).
  // Optional body { sources: ['jobs'], limit: 2 } for cost-controlled test runs.
  runDiscovery: (body = {}) => http('/api/discovery/run', {
    method: 'POST', body: JSON.stringify(body),
  }),
  // Live run controls: pause freezes spend, resume continues, cancel stops cleanly.
  pauseDiscovery: () => http('/api/discovery/pause', { method: 'POST' }),
  resumeDiscovery: () => http('/api/discovery/resume', { method: 'POST' }),
  cancelDiscovery: () => http('/api/discovery/cancel', { method: 'POST' }),
  // Delete discovered companies: { keys: [...] } or { all: true } for a clean slate.
  deleteCompanies: (body) => http('/api/discovery/delete', {
    method: 'POST', body: JSON.stringify(body),
  }),
  // Jobs stacking watch list: companies with a single open standard RCM role,
  // parked until a second opens. → { companies, count, stack_min, window_days }.
  parked: () => http('/api/discovery/parked'),
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

  // ── ABM target list ─────────────────────────────────────────────────────────
  // Upload the target workbook (.xlsx) as raw bytes; replaces the stored list.
  importAbm: (file) => http('/api/abm/import', {
    method: 'POST', headers: { 'Content-Type': 'application/octet-stream' }, body: file,
  }),
  abmSummary: () => http('/api/abm/summary'),
  abmMatches: () => http('/api/abm/matches'),

  // ── market-intelligence news ────────────────────────────────────────────────
  news: ({ topic, days = 30, limit = 200 } = {}) => {
    const q = new URLSearchParams({ days: String(days), limit: String(limit) });
    if (topic) q.set('topic', topic);
    return http(`/api/news?${q.toString()}`);
  },
  refreshNews: () => http('/api/news/refresh', { method: 'POST' }),

  // ── social: monitored LinkedIn accounts (Apify post-engagement) ─────────────
  socialTargets: () => http('/api/social/targets'),
  addSocialTarget: (body) => http('/api/social/targets', {
    method: 'POST', body: JSON.stringify(body),
  }),
  removeSocialTarget: (linkedin_url) => http('/api/social/targets', {
    method: 'DELETE', body: JSON.stringify({ linkedin_url }),
  }),
  // Event/conference keywords we search posts for, to find attendees.
  eventKeywords: () => http('/api/social/keywords'),
  addEventKeyword: (body) => http('/api/social/keywords', {
    method: 'POST', body: JSON.stringify(body),
  }),
  removeEventKeyword: (keyword) => http('/api/social/keywords', {
    method: 'DELETE', body: JSON.stringify({ keyword }),
  }),
  // Manual social scan with a date window: { window: "24h"|"week"|"month",
  // scope: "all"|"accounts"|"events" }. One run at a time.
  runSocial: (body = {}) => http('/api/social/run', {
    method: 'POST', body: JSON.stringify(body),
  }),

  // ── scoring phase ──────────────────────────────────────────────────────────
  frameworks: () => http('/api/scoring/frameworks'),
  scored: () => http('/api/scored'),
  account: (id) => http(`/api/account/${encodeURIComponent(id)}`),
  scoreAccount: (id) => http(`/api/account/${encodeURIComponent(id)}/score`, { method: 'POST' }),
  // Kick the on-demand deep-research dossier; poll account(id) until ready.
  generateDossier: (id) => http(`/api/account/${encodeURIComponent(id)}/dossier`, { method: 'POST' }),
  // Find ICP decision-makers + founder warm paths; poll account(id) until ready.
  findWarmIntros: (id) => http(`/api/account/${encodeURIComponent(id)}/warm-intros`, { method: 'POST' }),
  // Backfill warm intros across every scored account (Apollo free; green/yellow
  // also get paid school enrichment). Returns {scheduled, enrich_green_yellow, estimated_usd}.
  runAllWarmIntros: (force = false) => http(`/api/scoring/warm-intros/run-all${force ? '?force=true' : ''}`, { method: 'POST' }),
  scoringActivity: () => http('/api/scoring/activity'),
  // Spend summary for the cost meter (month-to-date vs budget, total, avg).
  scoringStats: () => http('/api/scoring/stats'),
  // Score parked (queued) accounts in a bounded background batch. Pass
  // { limit } to score a slice, or {} for all queued.
  scoreQueued: (body = {}) => http('/api/scoring/score-queued', {
    method: 'POST', body: JSON.stringify(body),
  }),
  // Clear every score back to 'queued' (non-destructive) to re-run + re-measure.
  resetScores: () => http('/api/scoring/reset', { method: 'POST', body: JSON.stringify({ confirm: true }) }),
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
