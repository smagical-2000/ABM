// ── Campaigns tab — DEMO SWAP (2026-07-22, Sunny: show the Campaign Automation
// prototype in the exec presentation, hide/restore later). ────────────────────
// The production v1 enrolment board is preserved verbatim in
// docs/archive/campaigns.v1.jsx.bak (and in git history — it lived here until
// the QA sweep flagged it as publicly served). To RESTORE:
// `cp ../../docs/archive/campaigns.v1.jsx.bak campaigns.jsx` (from this dir)
// and delete campaign-autopilot-demo.html.
//
// The prototype is a self-contained static HTML file (its own <style>/<script>,
// fully isolated) rendered in an iframe so it can't collide with the live app.
// It's a mock: sample data, no real enrolment. Reviewed with Sunny.
function CampaignsView({ pushToast }) {
  return (
    <div>
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '10px',
          padding: '10px 16px',
          marginBottom: '10px',
          borderRadius: '10px',
          border: '1px solid #f0c36d',
          background: '#fdf6e3',
          color: '#8a6d1a',
          fontSize: '13px',
          fontWeight: 600,
        }}
      >
        <span style={{ fontSize: '15px' }}>🧪</span>
        <span>
          Prototype preview — illustrative data only. Campaign automation is not live yet;
          nothing here enrolls, contacts, or emails anyone.
        </span>
      </div>
      <iframe
      title="Campaign Automation prototype"
      src="campaign-autopilot-demo.html"
      style={{
        width: '100%',
        height: 'calc(100vh - 240px)',
        minHeight: '640px',
        border: '0',
        borderRadius: '14px',
        background: '#fafafa',
        display: 'block',
      }}
      />
    </div>
  );
}
window.CampaignsView = CampaignsView;
