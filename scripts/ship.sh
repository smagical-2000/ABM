#!/usr/bin/env bash
# Fleet deploy with build-parity verification (2026-07-20).
#
# The July 10→20 incident: web services were redeployed repeatedly while both
# cron services silently ran 10-day-old code — minting identity twins and
# dropping heat events the whole time. Rule now: THERE IS ONE WAY TO DEPLOY,
# and it ships every service with the same BUILD_STAMP, then refuses to call
# it done until the stamp is verifiably serving.
#
# Usage: ./scripts/ship.sh          (from the repo root, on the branch to ship)
set -euo pipefail
cd "$(dirname "$0")/.."

SERVICES=(engagement-preview discovery-api discovery-cron linkedin-tofu-cron)

# The serve-verification below is the whole point of this script — with neither
# web URL set it would silently verify NOTHING (a vacuous pass), so refuse to
# ship at all rather than pretend.
if [[ -z "${ENGAGEMENT_APP_URL:-}" && -z "${DISCOVERY_API_URL:-}" ]]; then
  echo "✗ neither ENGAGEMENT_APP_URL nor DISCOVERY_API_URL is set — the /api/health" >&2
  echo "  stamp verification would be vacuous. Export at least one and re-run." >&2
  exit 1
fi

# Env-manifest gate (2026-07-28): a required var missing on ONE service is a
# silent no-op for weeks (REPLYIO_API_KEY absent on discovery-cron = 13 days of
# frozen Reply.io heat; the Clay bridge vars absent on linkedin-tofu-cron would
# have no-opped auto-dispatch forever). Same philosophy as the vacuous-verify
# refusal above: refuse to ship into a fleet we can see is misconfigured.
echo "── env-manifest gate: required vars per service (ops/env-manifest.json) ──"
if ! python3 scripts/check_env_manifest.py; then
  echo "✗ env-manifest gate failed — set the missing var(s) on the flagged service(s)" >&2
  echo "  (railway variables --set VAR=... --service <svc>), or update" >&2
  echo "  ops/env-manifest.json if the requirement truly changed. Not shipping." >&2
  exit 1
fi

STAMP="ship-$(date -u +%Y%m%dT%H%M%SZ)"
echo "$STAMP" > .build-stamp
echo "══ shipping build $STAMP to: ${SERVICES[*]} ══"

for svc in "${SERVICES[@]}"; do
  railway variables --set "BUILD_STAMP=$STAMP" --service "$svc" >/dev/null
  railway up --detach --service "$svc" | tail -1
done

echo "── waiting for all deployments to reach SUCCESS ──"
# Let the new deployments REGISTER first: polling immediately would read the
# PREVIOUS deployment's SUCCESS and declare victory before anything built.
sleep 15
for i in $(seq 1 60); do
  STATUSES=$(railway status --json 2>/dev/null | python3 -c "
import json,sys
d=json.load(sys.stdin)
svcs={s.strip() for s in '${SERVICES[*]}'.split()}
out=[]
for env in d.get('environments',{}).get('edges',[]):
    for si in env['node'].get('serviceInstances',{}).get('edges',[]):
        n=si['node']
        if n.get('serviceName') in svcs:
            out.append((n['serviceName'], (n.get('latestDeployment') or {}).get('status')))
print(';'.join(f'{a}={b}' for a,b in sorted(out)))")
  echo "  [$i] $STATUSES"
  case "$STATUSES" in
    *FAILED*|*CRASHED*) echo "✗ a deploy FAILED — fleet is NOT consistent"; exit 1 ;;
  esac
  if [[ "$(grep -o 'SUCCESS' <<<"$STATUSES" | wc -l | tr -d ' ')" == "${#SERVICES[@]}" ]]; then
    break
  fi
  sleep 10
done
# The loop above can EXHAUST without ever reaching all-SUCCESS — falling
# through to "verified" on a timeout would be the stale-fleet lie all over.
if [[ "$(grep -o 'SUCCESS' <<<"${STATUSES:-}" | wc -l | tr -d ' ')" != "${#SERVICES[@]}" ]]; then
  echo "✗ timed out waiting for all deployments to reach SUCCESS (last: ${STATUSES:-none})"
  exit 1
fi

echo "── verifying the WEB services actually serve $STAMP (/api/health) ──"
for url_var in ENGAGEMENT_APP_URL DISCOVERY_API_URL; do
  URL="${!url_var:-}"
  [[ -z "$URL" ]] && continue
  for i in $(seq 1 30); do
    GOT=$(curl -fsS "$URL/api/health" 2>/dev/null | python3 -c \
      "import json,sys; print(json.load(sys.stdin).get('build','?'))" || echo "?")
    [[ "$GOT" == "$STAMP" ]] && { echo "  ✓ $url_var serves $STAMP"; break; }
    sleep 5
  done
  [[ "${GOT:-}" != "$STAMP" ]] && { echo "✗ $url_var still serves ${GOT:-?}"; exit 1; }
done

echo "── crons verify themselves on next tick (I6-fleet heartbeat vs $STAMP) ──"
echo "✅ fleet shipped: $STAMP"
