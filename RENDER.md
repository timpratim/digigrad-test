# Deploy your own clone to Render

Each person runs their **own** gradphone instance on Render — their own profile,
voice, memory, and phone number, fully self-contained. One Web Service runs both
the bridge (phone calls) and the Telegram bot (onboarding + chat). Storage is a
small SQLite DB + recordings on a persistent disk. No tunnel, no shared server.

## What you need first
- A **Render account** (the deploy uses the paid **Starter** plan, ~$7/mo — the
  free tier sleeps and would drop calls).
- A **Telegram bot token** — create one with [@BotFather](https://t.me/BotFather)
  (`/newbot`), takes a minute.
- A **Gradium API key** (STT/TTS/voice cloning) — provided at the workshop.
- A **Twilio number + credentials** — `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`,
  `TWILIO_PHONE_NUMBER` (provided at the workshop; voice-only, no SMS).
- The LLM endpoint values (`LLM_BASE_URL`, `LLM_MODEL`) and `GRADIUM_BASE_URL` —
  copy from the workshop's sample `.env`. The LLM needs no key.
- A `BRIDGE_API_KEY` — any long random string (gates the bridge's internal API).

## Deploy
1. In the Render dashboard: **New → Blueprint**, connect this repo. Render reads
   `render.yaml`, creates the Web Service + disk, and prompts for the secret
   env vars listed there — paste your values.
2. Wait for the first build/deploy. Your service gets a stable URL:
   `https://<your-service>.onrender.com`. The app auto-fills `PUBLIC_HTTP_URL` /
   `PUBLIC_WS_URL` from it — you don't set those.
3. **Point your Twilio number at it:** in the Twilio console, set the number's
   **Voice webhook** to `https://<your-service>.onrender.com/twilio/voice` (HTTP
   POST). (A startup auto-register for this is a planned convenience.)

## Use it
1. Message your Telegram bot → `/register`.
2. Send a 15–20s **voice note** → confirm consent → your voice is cloned.
3. **Share your contact** with the bot so inbound calls route to your assistant.
4. Talk to your clone:
   - **Telegram**: send a voice note (voice reply) or type (text reply).
   - **Phone — your number calls you**: `/callme +<your-number>`.
   - **Phone — call in**: dial your Twilio number. From your registered phone →
     your assistant; from anyone else → your AI receptionist.
5. Optional features: set `LINKUP_API_KEY` (web search) and `GMAIL_ADDRESS` +
   `GMAIL_APP_PASSWORD` (email summaries) to enable those tools on calls.

## Notes
- **Single instance only** — the bridge holds in-memory call state; never scale >1.
- **Region**: `render.yaml` defaults to `frankfurt` (EU). Change to `oregon`/
  `virginia` for US numbers/lower latency.
- **Decommission**: delete the Render service (stops billing) and release the
  Twilio number.
