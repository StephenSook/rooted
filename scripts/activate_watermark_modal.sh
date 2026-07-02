#!/usr/bin/env bash
#
# One-command activation for the Rooted watermark decode service on Modal.
#
# What it does, end to end:
#   1. logs into Modal if needed (one browser approval, one time),
#   2. creates the shared-secret the service checks (from .env, never printed),
#   3. deploys infra/modal/watermark_service.py (first build bakes torch + the TrustMark weights,
#      so it is slow once, then cached),
#   4. health-checks the deployed service,
#   5. points the live rooted-api at it by setting exactly two env vars on Render (each set
#      INDIVIDUALLY, so none of your other env vars are touched), which triggers a redeploy,
#   6. polls the live /demo/remark-failover until the watermark half runs for real.
#
# Run once from the repo root:  bash scripts/activate_watermark_modal.sh
# Roll back any time by deleting ROOTED_WATERMARK_REMOTE_URL + ROOTED_WATERMARK_REMOTE_TOKEN on the
# rooted-api Render service; the endpoint returns to its honest "not run in this deployment" state.

set -euo pipefail
cd "$(dirname "$0")/.."

API="https://rooted-api-ubvc.onrender.com"
RENDER_SVC="srv-d901om9o3t8c73br93s0"  # rooted-api
SERVICE_FILE="infra/modal/watermark_service.py"
MODAL="uvx modal"

[ -f .env ] || { echo "run this from the repo root; .env not found"; exit 1; }
[ -f "$SERVICE_FILE" ] || { echo "missing $SERVICE_FILE"; exit 1; }

# Load the token + Render key from .env, then DROP the placeholder Modal vars so the modal CLI uses
# your real ~/.modal.toml login instead of the "your_mo..." placeholders in .env.
set -a; . ./.env; set +a
unset MODAL_TOKEN_ID MODAL_TOKEN_SECRET

: "${ROOTED_WATERMARK_REMOTE_TOKEN:?missing ROOTED_WATERMARK_REMOTE_TOKEN in .env}"
: "${RENDER_API_KEY:?missing RENDER_API_KEY in .env}"
TOKEN="$ROOTED_WATERMARK_REMOTE_TOKEN"

echo "==> 1/6 Modal login"
if ! $MODAL token current >/dev/null 2>&1; then
  echo "    a browser window will open; approve it, then this continues"
  $MODAL setup
else
  echo "    already logged in"
fi

echo "==> 2/6 Auth secret (rooted-watermark-auth)"
$MODAL secret create rooted-watermark-auth "ROOTED_WATERMARK_TOKEN=$TOKEN" --force >/dev/null 2>&1 \
  || $MODAL secret create rooted-watermark-auth "ROOTED_WATERMARK_TOKEN=$TOKEN" >/dev/null 2>&1 \
  || echo "    secret already exists, continuing"

echo "==> 3/6 Deploy (first build downloads torch + bakes the model; this is the slow step)"
DEPLOY_OUT="$($MODAL deploy "$SERVICE_FILE" 2>&1)"
echo "$DEPLOY_OUT"
URL="$(printf '%s\n' "$DEPLOY_OUT" | grep -oE 'https://[a-z0-9._-]+modal\.run' | head -1)"
[ -n "$URL" ] || { echo "could not find the deployed URL in the modal output above"; exit 1; }
echo "    deployed at: $URL"

echo "==> 4/6 Health check"
curl -fsS "$URL/healthz"; echo

echo "==> 5/6 Wiring rooted-api on Render (two vars, set individually, nothing else touched)"
for pair in "ROOTED_WATERMARK_REMOTE_URL=$URL" "ROOTED_WATERMARK_REMOTE_TOKEN=$TOKEN"; do
  key="${pair%%=*}"; val="${pair#*=}"
  curl -fsS -X PUT "https://api.render.com/v1/services/$RENDER_SVC/env-vars/$key" \
    -H "Authorization: Bearer $RENDER_API_KEY" -H "Content-Type: application/json" \
    -d "{\"value\": \"$val\"}" >/dev/null \
    || { echo "    Render API set for $key failed; set the two vars in the dashboard instead"; exit 1; }
  echo "    set $key"
done

echo "==> 6/6 Waiting for rooted-api to redeploy and the watermark half to go live (up to ~5 min)"
for _ in $(seq 1 30); do
  sleep 10
  attempted="$(curl -fsS "$API/demo/remark-failover" 2>/dev/null \
    | python3 -c 'import json,sys;print(json.load(sys.stdin).get("watermark",{}).get("attempted"))' 2>/dev/null || true)"
  if [ "$attempted" = "True" ]; then
    echo "    LIVE: the watermark half now runs the real model."
    curl -fsS "$API/demo/remark-failover" | python3 -m json.tool | sed -n '1,40p'
    echo
    echo "Done. /demo/remark-failover now runs BOTH halves live."
    exit 0
  fi
done
echo "    still redeploying; give it another minute, then check:"
echo "    curl -s $API/demo/remark-failover | python3 -m json.tool"
