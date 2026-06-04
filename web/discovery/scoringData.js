// Scoring helpers. FRAMEWORKS is the single source of truth, loaded from the API
// (GET /api/scoring/frameworks) at startup — the rubric definitions the scorer
// actually used. The score components read window.FRAMEWORKS / window.tierFor.

window.FRAMEWORKS = window.FRAMEWORKS || {};

// Resolve a tier band from a total. Bands are evaluated high -> low; the first
// whose `min` the total clears wins. (Sorted defensively in case the server
// order ever changes.)
window.tierFor = function tierFor(frameworkKey, total) {
  const f = window.FRAMEWORKS[frameworkKey];
  if (!f || !f.bands || !f.bands.length) return { band: 'low', label: '—' };
  const bands = [...f.bands].sort((a, b) => b.min - a.min);
  for (const b of bands) if (total >= b.min) return { band: b.band, label: b.label };
  return bands[bands.length - 1];
};
