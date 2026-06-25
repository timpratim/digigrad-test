# Deployment handoff — gradphone digital-clone bridge

**Audience:** Gradium deployment team.
**Goal:** Host the gradphone bridge as a service inside the existing `gradium-serve`
infrastructure, reachable at a stable public URL (e.g. `https://demo.gradium.ai/digitalclone`).
**Author context:** This is the backend for the "Build Your Digital Clone" workshop.
Single outbound/inbound voice agent: users clone their voice via Telegram, then a call
is dispatched where the agent speaks in their cloned voice.

---

## TL;DR for the deployment team

Deploy it **exactly like a gradbot example**: a single-replica Helm chart behind Traefik,
path-routed with `stripPrefix`. It is a normal Python 3.12 FastAPI app — **but it is
stateful and WebSocket-heavy**, so a few things differ from a stateless API. Read the
"Non-negotiables" section; those are what break it on the first call if missed.

Recommended target: **Scaleway demo cluster** (`k8s-par-site-and-demo-production`),
mirroring `deployment/helm/gradbot/`.

---

## What the service is

- **Framework:** FastAPI on `uvicorn[standard]`, Python **3.12 exactly** (uses stdlib
  `audioop` for audio resampling; 3.13 removed it).
- **Listens on:** one HTTP port (default `8082`), serving both HTTP webhooks **and** a
  long-lived WebSocket endpoint.
- **Dependency note (build):** the `gradbot` PyPI package (0.1.8) ships a
  `cp312 manylinux_2_17_x86_64` wheel — confirmed. A `python:3.12-slim` (Debian, glibc
  ≥ 2.17) `linux/amd64` base image installs it with **no Rust toolchain**. No native
  system packages needed.
- **Processes:** there are **two**:
  1. `uvicorn gradphone.bridge:app` — the public HTTP+WS server (this is what needs ingress).
  2. `python -m gradphone.bot` — the Telegram bot (long-polls Telegram; **no inbound
     webhook, no public endpoint, no ingress**). Optional but expected for the workshop.
  Simplest topology: **two containers in one Pod**, sharing storage.

## Public endpoints (what Twilio hits)

- `POST /twilio/voice` — returns TwiML telling Twilio to open the media stream.
- `POST /twilio/status` — Twilio status callbacks.
- `WS /twilio/stream` — **long-lived WebSocket** carrying the live audio (2–10 min per call).
- Plus an operator/tenant web dashboard (`/ui/*`, magic-link auth) — internal-facing.

---

## Non-negotiables (these break it on the first call if wrong)

1. **WebSockets must pass through.** Twilio Media Streams hold a WS open for the whole
   call. Traefik handles this fine (the gradbot path already runs WS with 24h timeouts) —
   just confirm no idle timeout below the max call length cuts it off.

2. **Single replica, `Recreate` strategy.** The bridge keeps **in-memory call state**
   (`_PENDING` / `_ACTIVE` dicts keyed by call/room). Two replicas split-brain: a Twilio
   WS upgrade landing on the wrong pod won't find its pending call and fails. Set
   `replicaCount: 1` and deployment `strategy: Recreate` (NOT RollingUpdate — rolling
   would briefly run two pods). Accept a few seconds of downtime on redeploy.
   - *This is single-instance by design today. Horizontal scaling would require moving
     that state to Redis + sticky routing — out of scope, not needed.*

3. **`PUBLIC_HTTP_URL` / `PUBLIC_WS_URL` must equal the exact external, path-prefixed URL.**
   Twilio verifies every request with an HMAC-SHA1 signature computed over the **exact URL
   it called**. The bridge reconstructs that URL from these env vars. With a path prefix:
   ```
   PUBLIC_HTTP_URL=https://demo.gradium.ai/digitalclone
   PUBLIC_WS_URL=wss://demo.gradium.ai/digitalclone
   ```
   If these don't include `/digitalclone` (or don't match the real host), **every webhook
   returns 403**. This is the single most likely cause of a failed first call.

4. **`stripPrefix: ["/digitalclone"]` middleware** on the IngressRoute (the gradbot chart
   already does this for `/gradbot`). The app must receive `/twilio/voice`, not
   `/digitalclone/twilio/voice`. Item 3 and item 4 must agree: the bridge emits TwiML
   pointing at `PUBLIC_WS_URL`, so the external path and the strip must be consistent.

5. **One service = one Twilio webhook = many users.** This is multi-tenant *inside one
   process*. There is **one** public path, not one-per-user. Users are keyed by `tenant_id`
   internally and routed by caller-ID (inbound) / explicit id (outbound). Any per-user
   `/digitalclone/<id>` URL is a **dashboard view**, not a separate deployment. Do not
   provision one ingress/service per user.

---

## Storage

Two paths hold state today (both default under the home dir):

| Path | Contents | If lost |
| --- | --- | --- |
| `~/.gradphone/gradphone.db` (SQLite; override via `GRADPHONE_DB`) | tenant registry, call history, agent memory | users must re-register, history/memory gone |
| `~/.openclaw/workspace/` | call recordings (`*.wav`), transcripts, per-call timelines | recordings/transcripts gone |

**Decision for this deployment: Postgres (done — the app layer is dual-backend).**
The persistence layer (`db.py` + `tenants.py` + `memory.py` + `web.py`) now runs on
SQLAlchemy and supports SQLite *or* Postgres, selected by env — matching gradium-serve's
`api` service (psycopg3 + SQLAlchemy + Alembic). Nothing further is needed in the app.

What the deployment team must wire up:
- **Connection** via the standard `DATABASE_*` secret keys in `app-secrets`
  (`DATABASE_TYPE=postgresql`, `DATABASE_HOST/PORT/USER/PASSWORD`, `DATABASE_NAME`), or a
  single `DATABASE_URL=postgresql+psycopg://…`. Same convention as the `api` chart.
- **Migrations** via an **init-container** running `alembic upgrade head` before the app
  starts — identical to `deployment/helm/api/templates/deployment.yaml`. The Alembic
  config (`alembic.ini` + `migrations/`) is in the repo and reads the same `DATABASE_*`
  env. (The app also calls `metadata.create_all` on SQLite for local dev; on Postgres it
  defers to Alembic unless `DB_CREATE_ALL=1` is set.)
- **Recordings** (`~/.openclaw/workspace/`) are still local disk — give the pod a small
  PVC for those, or accept they're ephemeral. Only the *recordings/transcripts* need the
  volume now; tenants/calls/memory live in Postgres.

Local dev/testing is unaffected: with no `DATABASE_*` set, it's SQLite at `GRADPHONE_DB`
with the schema auto-created — zero setup.

---

## Configuration (env / secrets)

Load into the existing `app-secrets` Kubernetes secret pattern:

- `PUBLIC_HTTP_URL`, `PUBLIC_WS_URL` — see non-negotiable #3.
- `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, Twilio from-number — needed for HMAC + dialing.
  (Note: workshop currently on a Twilio **trial** account — must be upgraded to paid before
  the event; trial adds a preamble + keypress and only dials verified numbers.)
- `GRADIUM_API_KEY` — Gradium STT/TTS/voice cloning.
- `BRIDGE_API_KEY` — signs the operator dashboard magic links and gates internal endpoints.
- `TELEGRAM_*` — for the bot process.
- `GRADBOT_MAX_CONCURRENT=3` — Gradium STT per-account concurrency cap; keep at 3 until
  Gradium raises it. **This caps simultaneous calls** — workshop demos must run as a queue,
  not all at once.
- `MAX_CALL_DURATION_SECONDS=180` — workshop call cap.
- (If Postgres) a `DATABASE_URL` / connection vars instead of `GRADPHONE_DB`.

---

## Chart shape (mirror `deployment/helm/gradbot/`)

The gradbot chart is the template. Differences for this service:

- `replicaCount: 1` (keep) + deployment `strategy.type: Recreate` (add).
- Ingress `basePath: /digitalclone`, with the `stripPrefix` middleware.
- Container port = bridge port (`8082` or whatever the image exposes).
- A liveness/readiness probe on a lightweight HTTP path (confirm which with app owner;
  do **not** point it at a Twilio route).
- Volume mount for storage (PVC interim) or Postgres connection (target).
- Second container in the Pod for `python -m gradphone.bot` (no port, no ingress),
  sharing any storage volume.
- Image built `--platform linux/amd64`, pushed to the same registries gradbot uses
  (Scaleway `rg.fr-par.scw.cloud/gradium/...`).

There is **no Dockerfile in the gradphone repo yet** — the app owner needs to provide one
(it's trivial: `python:3.12-slim` + `pip install -e .` + the two run commands), or the
deployment team can add a standard one. Either way, agree who owns it.

---

## Deploy / verify flow

1. Build + push the `linux/amd64` image to the gradium registry.
2. Add/enable the chart in the target environment values (mirror how `gradbot.enabled`
   is toggled), set `PUBLIC_*`, secrets, ingress `basePath`.
3. Point the Twilio number's voice webhook at `https://demo.gradium.ai/digitalclone/twilio/voice`.
4. **Smoke test that actually exercises the chain:** place one real end-to-end call against
   the hosted URL and confirm (a) no 403 on the webhook (validates `PUBLIC_*` + stripPrefix),
   (b) the WS stays open for the full call, (c) audio is two-way. This is the only test that
   proves the HMAC/prefix/WebSocket chain — do it before the workshop, not on the day.

---

## What to escalate to the app owner (not infra decisions)

- **SQLite → Postgres migration** is application code; schedule it with Pratim.
- **Dockerfile ownership** — decide app-side or infra-side.
- **Twilio paid-account upgrade** before the workshop.
- **Health-check endpoint** to probe — confirm the right path.
