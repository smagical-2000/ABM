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
  panel: ({ status = 'qualified', segment, signal_type } = {}) => {
    const q = new URLSearchParams({ status });
    if (segment && segment !== 'all') q.set('segment', segment);
    if (signal_type && signal_type !== 'all') q.set('signal_type', signal_type);
    return http(`/api/panel?${q.toString()}`);
  },
  company: (key) => http(`/api/company/${encodeURIComponent(key)}`),
  promote: (key) => http(`/api/company/${encodeURIComponent(key)}/promote`, { method: 'POST' }),
  defer: (key) => http(`/api/company/${encodeURIComponent(key)}/defer`, { method: 'POST' }),
  reject: (key, reason) =>
    http(`/api/company/${encodeURIComponent(key)}/reject`, {
      method: 'POST',
      body: JSON.stringify({ reason }),
    }),
};
