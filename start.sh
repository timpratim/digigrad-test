#!/usr/bin/env bash
# Launch the bridge (foreground, on $PORT) + the Telegram bot (background,
# auto-restart) in one container. Used as the Docker CMD on Render.
set -euo pipefail

# Render assigns $PORT and routes external traffic (incl. Twilio) to it.
export PORT="${PORT:-8082}"

# Twilio needs a public HTTPS/WSS URL. On Render, RENDER_EXTERNAL_URL is the
# stable service URL (https://<service>.onrender.com) — derive the public URLs
# from it unless they were set explicitly. This is what replaces the tunnel.
if [ -z "${PUBLIC_HTTP_URL:-}" ] && [ -n "${RENDER_EXTERNAL_URL:-}" ]; then
  export PUBLIC_HTTP_URL="$RENDER_EXTERNAL_URL"
fi
if [ -z "${PUBLIC_WS_URL:-}" ] && [ -n "${PUBLIC_HTTP_URL:-}" ]; then
  export PUBLIC_WS_URL="wss://${PUBLIC_HTTP_URL#https://}"
fi

# The bot reaches the bridge over loopback on the same port Render routes to.
export GRADBOT_BRIDGE_URL="http://127.0.0.1:${PORT}"

echo "gradphone start: PORT=$PORT PUBLIC_HTTP_URL=${PUBLIC_HTTP_URL:-<unset>}"

# Telegram bot in the background, auto-restarting if it ever exits.
( while true; do
    python -m gradphone.bot || echo "bot exited ($?), restarting in 2s"
    sleep 2
  done ) &

# Bridge in the foreground = container's main process.
exec uvicorn gradphone.bridge:app --host 0.0.0.0 --port "${PORT}"
