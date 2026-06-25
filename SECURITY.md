# Security model

gradphone places real phone calls and synthesizes cloned voices, so a
misconfigured deployment is dangerous. The code is designed to **fail closed**:
required secrets have no built-in defaults, and security gates deny by default
when their configuration is missing.

## Required secrets (the app refuses to run without them)

| Variable | Why it's required |
| --- | --- |
| `BRIDGE_API_KEY` | Bearer auth for all control endpoints **and** the signing key for web session cookies + magic links. Must be ≥16 chars. Generate: `python -c "import secrets; print(secrets.token_urlsafe(36))"`. |
| `TWILIO_AUTH_TOKEN` | Verifies the HMAC signature on every Twilio webhook (and the Media Streams upgrade). Without it, `/twilio/*` rejects requests. |
| `AGENT_VOICE_ID` | Default outbound voice. No default is shipped — a voice UID is scoped to a Gradium account. |
| `ALLOWED_TELEGRAM_IDS` **or** `WORKSHOP_CODE` | Telegram authorization. With neither set, the bot refuses every user. |

## Authorization

- **Control plane** (`/dial`, `/campaign`, `/history`, `/tenants`, `/result`,
  `/calls/live`, `/diagnostics`): `BRIDGE_API_KEY` bearer or an operator session
  cookie, compared in constant time. Fails closed if the key is unset.
- **Telegram bot**: a group `-1` gatekeeper enforces `ALLOWED_TELEGRAM_IDS`
  before any command. If that list is empty it falls back to `WORKSHOP_CODE`-gated
  self-serve registration; if both are empty it denies everyone.
- **Outbound destinations**: default-closed. Every dial funnels through
  `dispatch_gradbot_call`, which refuses any number not in `OUTBOUND_ALLOWLIST`
  unless `ALLOW_ARBITRARY_OUTBOUND=true`.
- **Web dashboard tenants**: scoped to their own `tenant_id`; result/audio
  endpoints check ownership; SQL is parameterized; templates auto-escape.

## Local development

For localhost-only work without the real secrets, set `ALLOW_INSECURE_LOCAL=1`.
This disables auth, Twilio signature checks, and the Telegram allow-list, and
prints loud warnings. **Never set it on a public host.** Use `INSECURE_COOKIES=1`
only to drop the cookie `Secure` flag for local HTTP.

## Known limitations / hardening backlog

These are mitigated but not fully closed; track before relying on this in
production beyond a demo:

- **Magic links** are stateless and replayable within their 5-minute window, and
  the token rides in a `GET` query string (lands in access logs). Consider
  single-use tokens and a `POST` exchange.
- **No CSRF token** on dashboard `POST`s — protection currently relies on
  `SameSite=Strict` cookies. Add a double-submit token for defense in depth.
- **Inbound caller-ID is advisory**, not authentication. The inbound assistant
  persona (off by default, `ENABLE_INBOUND=false`) keys off the spoofable `From`
  number; gate any sensitive disclosure behind a spoken PIN before enabling.
- **Call transcripts and phone numbers** may appear in stdout logs. Treat the log
  stream as sensitive; redact before shipping logs anywhere shared.
- Uploaded clone audio has no hard size cap; `LLM_BASE_URL` should be `https://`.

## Reporting

Found a vulnerability? Please open a private report rather than a public issue.
