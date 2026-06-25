#!/usr/bin/env bash
# Restart the cloudflared quick tunnel and re-sync everything that depends on
# its (ephemeral) URL: PUBLIC_* in .env, the Twilio voice webhooks, and the
# bridge process. Quick tunnels get a NEW hostname on every start — without
# this re-sync Twilio's HMAC verification 403s and inbound calls fail.
#
# Usage: scripts/tunnel_up.sh
set -euo pipefail

cd "$(dirname "$0")/.."
ENV_FILE=.env
TUNNEL_LOG=/tmp/cf_tunnel.log
BRIDGE_LOG=/tmp/bridge.log
PORT=8082

get_env() { grep -E "^$1=" "$ENV_FILE" | head -1 | cut -d= -f2-; }

echo "→ restarting cloudflared quick tunnel"
pkill -f "cloudflared tunnel --url" 2>/dev/null || true
sleep 1
nohup cloudflared tunnel --url "http://localhost:$PORT" > "$TUNNEL_LOG" 2>&1 &
disown

URL=""
for _ in $(seq 1 30); do
  URL=$(grep -Eo 'https://[a-z0-9-]+\.trycloudflare\.com' "$TUNNEL_LOG" | head -1 || true)
  [ -n "$URL" ] && break
  sleep 1
done
[ -n "$URL" ] || { echo "✗ tunnel URL never appeared (see $TUNNEL_LOG)"; exit 1; }
HOST=${URL#https://}
echo "→ tunnel up: $URL"

echo "→ updating $ENV_FILE"
sed -i '' -E "s|^PUBLIC_HTTP_URL=.*|PUBLIC_HTTP_URL=https://$HOST|" "$ENV_FILE"
sed -i '' -E "s|^PUBLIC_WS_URL=.*|PUBLIC_WS_URL=wss://$HOST|" "$ENV_FILE"

SID=$(get_env TWILIO_ACCOUNT_SID)
TOKEN=$(get_env TWILIO_AUTH_TOKEN)
if [ -n "$SID" ] && [ -n "$TOKEN" ]; then
  echo "→ re-pointing Twilio voice webhooks that use a trycloudflare host"
  curl -s -u "$SID:$TOKEN" \
    "https://api.twilio.com/2010-04-01/Accounts/$SID/IncomingPhoneNumbers.json?PageSize=50" |
  python3 -c '
import sys, json
for n in json.load(sys.stdin).get("incoming_phone_numbers", []):
    if "trycloudflare.com" in (n.get("voice_url") or ""):
        print(n["sid"], n["phone_number"])' |
  while read -r pn_sid number; do
    curl -s -o /dev/null -u "$SID:$TOKEN" \
      -d "VoiceUrl=https://$HOST/twilio/voice" -d "VoiceMethod=POST" \
      "https://api.twilio.com/2010-04-01/Accounts/$SID/IncomingPhoneNumbers/$pn_sid.json"
    echo "   $number → https://$HOST/twilio/voice"
  done
else
  echo "⚠ Twilio creds not in $ENV_FILE — webhooks NOT updated"
fi

echo "→ restarting bridge on :$PORT"
lsof -ti ":$PORT" | xargs kill 2>/dev/null || true
sleep 2
nohup .venv/bin/uvicorn gradphone.bridge:app --port "$PORT" > "$BRIDGE_LOG" 2>&1 &
disown
sleep 5

# Fresh quick-tunnel hostnames take a few seconds to propagate in DNS.
CODE=000
for _ in $(seq 1 12); do
  CODE=$(curl -s -m 10 -o /dev/null -w '%{http_code}' "https://$HOST/healthz" || true)
  [ "$CODE" = "200" ] && break
  sleep 5
done
if [ "$CODE" = "200" ]; then
  echo "✓ bridge healthy through tunnel: https://$HOST"
else
  echo "✗ healthz via tunnel returned $CODE (bridge log: $BRIDGE_LOG)"
  exit 1
fi
