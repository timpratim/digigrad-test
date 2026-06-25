# gradphone — your voice digital clone

Clone your voice once, then **talk to your clone** — by text or voice note on
Telegram, or on a real phone call. Your clone can **call you** (`/callme`) and
chat in your own voice, **answer your phone number** (you reach your assistant;
anyone else reaches an AI receptionist that takes a message), remember things
about you across conversations, **search the live web**, and **summarize your
email**.

Built on the **gradbot** framework (speech-to-text → LLM → text-to-speech) wired
to **Twilio** for phone calls and **Telegram** for chat. One small service runs
the whole thing.

This guide assumes **no prior setup**. Follow it top to bottom and you'll have a
working clone. There are two ways to run it — pick one:

- **[Option A — Deploy to Render](#option-a--deploy-to-render-recommended)** (recommended): no code, no local install. ~15 min.
- **[Option B — Run locally](#option-b--run-locally-for-developers)** (for developers): Python on your machine.

---

## What your clone can do

| Capability | How you use it | Status |
|---|---|---|
| **Clone your voice** | Send a 15–20s voice note on Telegram | ✅ |
| **Text chat** | Type to the bot — it replies in text | ✅ |
| **Voice-note chat** | Send a voice note — it replies in *your* cloned voice | ✅ |
| **Real-time translation** (`/translate`) | Send a voice note — hear it translated, in *your* cloned voice | ✅ |
| **Remembers you** | Tell it facts ("I'm vegetarian"); it recalls them later | ✅ |
| **Calls you** (`/callme`) | It phones you and talks in your voice | ✅ |
| **Answers your number** | You call in → your assistant; a stranger calls → AI receptionist takes a message | ✅ |
| **Web search** (on calls) | "Search the web for…" — live, sourced answers | ✅ (needs a Linkup key) |
| **Email summary** (on calls) | "Summarize my recent emails" | ✅ (needs a Gmail app password) |
| **Natural turn-taking** | Interrupt the clone mid-sentence (barge-in) | ✅ |

> Note: web search and email summary currently work **on phone calls**, not in the
> Telegram chat.

---

## Before you start: accounts & keys you'll need

You'll collect a handful of values and paste them into the app's configuration.
Here's **each one, why it's needed, and exactly how to get it.** Get these first.

### 1. Telegram bot token — **required**
This is your clone's chat interface.
1. In Telegram, open a chat with **@BotFather**.
2. Send `/newbot`, pick a name and a username (must end in `bot`).
3. BotFather replies with a **token** like `8943069891:AAГ…`. Copy it.
- → `TELEGRAM_BOT_TOKEN`

### 2. Gradium API key — **required**
Powers voice cloning, speech-to-text, and text-to-speech.
- Get a key from your Gradium account (provided to you at the workshop, or from
  the Gradium dashboard). It looks like `gsk_…`.
- → `GRADIUM_API_KEY`

### 3. LLM endpoint — **required**
The "brain" that generates replies. It must be an **OpenAI-compatible** endpoint.
- **Easiest:** use the Gradium-hosted LLM values provided to you — a base URL and
  a model name. This endpoint needs **no separate key**.
  - → `LLM_BASE_URL` (e.g. `https://…/v1`) and `LLM_MODEL` (e.g. `google/gemma-…`)
- **Or your own:** use OpenAI by setting `OPENAI_API_KEY` (then leave `LLM_BASE_URL`
  blank), or any other OpenAI-compatible host (Groq, Together, etc.).

### 4. Twilio — **required for phone calls**
Phone calls in and out. (You can chat on Telegram without this, but `/callme` and
inbound calls need it.)
1. Create an account at **twilio.com** and **upgrade it to a paid account** (the
   free trial adds a "press a key" preamble and only dials verified numbers).
2. From the Twilio **Console dashboard**, copy:
   - **Account SID** (starts with `AC…`) → `TWILIO_ACCOUNT_SID`
   - **Auth Token** (click to reveal) → `TWILIO_AUTH_TOKEN`
3. **Buy a phone number** (Console → Phone Numbers → Buy a number) with **Voice**
   capability. It looks like `+1XXXXXXXXXX`. → `TWILIO_PHONE_NUMBER`
   - This is **voice only** — no SMS — so no A2P/10DLC registration is required.
- You'll point this number's **Voice webhook** at your app later (a step below).

### 5. Bridge API key — **required**
A password that protects the app's internal API. **Make one up** — any long random
string (e.g. run `openssl rand -hex 24`).
- → `BRIDGE_API_KEY`

### 6. Linkup key — *optional* (enables web search on calls and Telegram text chat)
1. Sign up at **app.linkup.so** and copy your API key.
- → `LINKUP_API_KEY` (leave blank to disable web search)

### 7. Gmail app password — *optional* (enables email summary on calls)
This is **not** your normal Gmail password.
1. Turn on **2-Step Verification** on the Google account (myaccount.google.com/security).
2. Go to **myaccount.google.com/apppasswords**, create one named "gradphone".
3. Google shows a **16-character** password — copy it (spaces don't matter).
- → `GMAIL_ADDRESS` (your address) and `GMAIL_APP_PASSWORD` (the 16-char password)

---

## Option A — Deploy to Render (recommended)

No local install. You get a stable public URL automatically, which Twilio needs.

> **Use a paid (Starter) instance.** The free tier sleeps after inactivity and
> would drop calls. ~$7/month.

### Step 1 — Get the code into your own repo
- **Fork** this repository to your own GitHub account (GitHub → Fork), or push a
  copy to a repo you control. Render deploys from a GitHub repo you own.

### Step 2 — Create the service from the Blueprint
1. Create an account at **render.com** and connect your GitHub.
2. Click **New → Blueprint**, and select your forked repo.
3. Render reads the included **`render.yaml`** and sets up one always-on web
   service with a small disk. It will **prompt you for the secret values** — paste
   the keys you collected above:
   - `TELEGRAM_BOT_TOKEN`, `GRADIUM_API_KEY`, `GRADIUM_BASE_URL` (if provided),
     `LLM_BASE_URL`, `LLM_MODEL`, `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`,
     `TWILIO_PHONE_NUMBER`, `BRIDGE_API_KEY`
   - Set `ALLOW_ARBITRARY_OUTBOUND` to `true` (so it can call *your* number), or
     instead set `OUTBOUND_ALLOWLIST` to your phone number in `+E.164` form.
   - Optional: `LINKUP_API_KEY`, `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD`.
4. Click **Apply**. Render builds the Docker image and deploys (~2 min).
   - The non-secret settings (always-on, disk, `ENABLE_INBOUND=true`, the voice
     tuning, and `TWILIO_MACHINE_DETECTION=Disable`) come from `render.yaml`
     automatically — you don't type those.

### Step 3 — Note your public URL
When the deploy is live, your service has a URL like
`https://<your-service>.onrender.com`. The app fills in `PUBLIC_HTTP_URL` /
`PUBLIC_WS_URL` from it automatically — you don't set those.

Check it's up: open `https://<your-service>.onrender.com/healthz` — you should see
`{"status":"ok","gradbot_installed":true,…}`.

### Step 4 — Point your Twilio number at the app
In the Twilio Console → **Phone Numbers → your number → Voice configuration**:
- Set **"A call comes in"** to **Webhook**, URL:
  `https://<your-service>.onrender.com/twilio/voice`, method **HTTP POST**. Save.

Now jump to **[First run](#first-run--set-up-and-use-your-clone)**.

---

## Option B — Run locally (for developers)

You need your own machine reachable by Twilio, which means a tunnel.

### Prerequisites
- **Python 3.12 exactly** (not 3.11, not 3.13). Check: `python3.12 --version`.
- **ffmpeg** installed (used to process voice notes). macOS: `brew install ffmpeg`.
- A tunnel tool: **cloudflared** (`brew install cloudflared`) or ngrok.

### Step 1 — Install
```bash
git clone <your-repo-url> && cd gradphone-aie
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e .
```

### Step 2 — Configure
```bash
cp .env.example .env
```
Open `.env` and fill in the keys you collected (see the
[reference table](#environment-variables-reference) below). At minimum:
`TELEGRAM_BOT_TOKEN`, `GRADIUM_API_KEY`, `LLM_BASE_URL` + `LLM_MODEL` (or
`OPENAI_API_KEY`), the three `TWILIO_*` values, `BRIDGE_API_KEY`. Also set
`ENABLE_INBOUND=true`, `ALLOW_ARBITRARY_OUTBOUND=true`, and
`TWILIO_MACHINE_DETECTION=Disable`.

### Step 3 — Start a tunnel and set the public URL
```bash
cloudflared tunnel --url http://localhost:8082
```
It prints a URL like `https://something.trycloudflare.com`. In `.env` set:
```
PUBLIC_HTTP_URL=https://something.trycloudflare.com
PUBLIC_WS_URL=wss://something.trycloudflare.com
```
> Quick tunnels get a **new URL every restart** — update both lines (and the
> Twilio webhook in Step 5) whenever it changes.

### Step 4 — Run the two processes (two terminals)
```bash
# Terminal 1 — the bridge (phone calls + web API)
uvicorn gradphone.bridge:app --host 0.0.0.0 --port 8082

# Terminal 2 — the Telegram bot
python -m gradphone.bot
```
Check: `curl http://localhost:8082/healthz`

### Step 5 — Point your Twilio number at the tunnel
Twilio Console → your number → Voice webhook (HTTP POST):
`https://something.trycloudflare.com/twilio/voice`.

---

## First run — set up and use your clone

Do this in Telegram with **the bot you created** (search its username).

1. **Register:** send `/register`.
   - If the operator set a workshop code, send `/register <code>`.
2. **Clone your voice:** send a **15–20 second voice note** of you talking. Tap
   **"✅ Yes, clone my voice"** when asked to confirm it's your own voice. Wait for
   the "voice ready" confirmation.
3. **Share your phone number:** use Telegram's **share-contact** to send the bot
   your own contact. This links your caller ID so that when *you* call in, you
   reach **your assistant** (not the receptionist).
4. **Try it:**
   - **Text:** just type a message — the clone replies in text.
   - **Voice note:** send one — the clone replies in *your* voice.
   - **Translate:** send `/translate`, pick a language, then send a voice note —
     hear yourself speak it in that language, in your own cloned voice.
   - **Call you:** send `/callme +<your-number>` — your phone rings and your clone
     talks to you. Try interrupting it mid-sentence; try "what do you remember
     about me?"; on a call, "search the web for today's weather in Paris" or
     "summarize my recent emails".
   - **Call in:** dial your Twilio number from your phone → you reach your
     assistant. From any other phone → the AI receptionist takes a message.

### Other Telegram commands
| Command | What it does |
|---|---|
| `/register [code]` | Become a tenant (clone owner). |
| `/callme <+number>` | Your clone calls that number and converses. |
| `/translate` | Pick a language, then send a voice note — get it back translated in your cloned voice. |
| `/voice` | Show your current cloned voice. |
| `/clear_voice` | Delete your clone so you can re-record. |
| `/history` | Your recent calls. |
| `/status` | Calls currently in progress. |
| `/whoami` | Your Telegram ID + registration status. |

---

## Environment variables reference

Set these in Render's dashboard (Option A) or in `.env` (Option B).

### Required
| Variable | What it is |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Your bot token from @BotFather. |
| `GRADIUM_API_KEY` | Gradium key (voice clone, STT, TTS). |
| `LLM_BASE_URL` + `LLM_MODEL` | OpenAI-compatible LLM endpoint + model. (Or use `OPENAI_API_KEY`.) |
| `TWILIO_ACCOUNT_SID` | From the Twilio console (`AC…`). |
| `TWILIO_AUTH_TOKEN` | From the Twilio console. |
| `TWILIO_PHONE_NUMBER` | Your bought Twilio number (`+E.164`). |
| `BRIDGE_API_KEY` | Any long random string you choose. |
| `PUBLIC_HTTP_URL` / `PUBLIC_WS_URL` | Public URLs Twilio reaches you at. **Auto-set on Render**; set manually for local. |

### Recommended / common
| Variable | Default | Notes |
|---|---|---|
| `ENABLE_INBOUND` | `false` | Set `true` so the number answers incoming calls. |
| `ALLOW_ARBITRARY_OUTBOUND` | `false` | Set `true` to let it dial any number (or use `OUTBOUND_ALLOWLIST`). |
| `OUTBOUND_ALLOWLIST` | — | Comma-separated `+E.164` numbers it's allowed to dial. |
| `TWILIO_MACHINE_DETECTION` | `Enable` | **Set `Disable`** for `/callme`/inbound (a human answers; AMD otherwise misfires to voicemail). |
| `WORKSHOP_CODE` | — | If set, `/register` requires this code. |
| `GRADBOT_MAX_CONCURRENT` | `3` | Max simultaneous calls (Gradium caps this per account). |
| `MAX_CALL_DURATION_SECONDS` | `600` | Hard hang-up after this many seconds. |

### Optional features
| Variable | Enables |
|---|---|
| `LINKUP_API_KEY` | Web search on calls and in Telegram text chat. |
| `GMAIL_ADDRESS` + `GMAIL_APP_PASSWORD` | Email summary on calls. |
| `GRADIUM_URL` (default `https://satellite-scw.gradium.ai/api`) | Host for the `/translate` speech-to-speech engine (separate from `GRADIUM_BASE_URL`). |
| `GRADIUM_TRANSLATE_VOICE_ID` | Force a specific voice for translated output; required only to translate into a language with no built-in voice (built-ins: en, fr, es, de, pt). |
| `BARGE_IN_GUARD_S` (default `1.0`) | Seconds at the start of each clone turn where interruptions are ignored (raise if barge-in feels too twitchy). |
| `GRADBOT_SILENCE_TIMEOUT_S` (default `2.0`) | How long a pause ends the caller's turn (lower = snappier). |

The full annotated list is in **`.env.example`**.

---

## Troubleshooting

| Symptom | Cause & fix |
|---|---|
| **`/callme` connects then ends in a few seconds (silence)** | Twilio Answering Machine Detection misread your "hello" as voicemail. Set `TWILIO_MACHINE_DETECTION=Disable`. |
| **Call answers but you hear nothing / it drops** | The media-stream WebSocket isn't connecting. Make sure `PUBLIC_WS_URL` is the exact public host as `wss://…` (on Render it's auto-set; locally it must match your live tunnel and the Twilio webhook). |
| **Twilio webhook returns 403** | `PUBLIC_HTTP_URL` doesn't match the URL Twilio actually called. Re-point the Twilio webhook and update `PUBLIC_HTTP_URL`. |
| **Bot logs `telegram.error.Conflict … only one bot instance`** | The same `TELEGRAM_BOT_TOKEN` is running in two places. Stop the other one — one bot process per token. |
| **You call in but get the receptionist, not your assistant** | Your caller ID isn't linked. Share your contact with the bot (First-run step 3). |
| **"Couldn't hear that" on a voice note** | `ffmpeg` missing (local) — install it. On Render it's already in the image. |
| **Calls stop connecting on a free Render instance** | Free instances sleep. Use the paid Starter plan (always-on). |
| **Web search / email "didn't work" in Telegram chat** | Those tools run on **phone calls**, not Telegram chat. Use `/callme` and ask there. |

To see what's happening, check your logs: in Render, open the service's **Logs**
tab; locally, watch the two terminal windows.

---

## Known limits

- **One owner per deployment.** Each running instance is a single person's clone.
  (For many people, each person deploys their own.)
- **Languages:** English, French, Portuguese.
- **Web search & email** are available on calls, not in Telegram chat (yet).
- **Concurrency** is capped by your Gradium account (default 3 simultaneous calls).
- **Fillers** (a sound while the clone "thinks") are experimental and off by
  default (`ENABLE_FILLERS=0`).

---

## How it works (brief)

```
Telegram  ──voice/text──►  bot  ──►  Gradium STT → LLM → Gradium TTS  ──►  reply
                                  └─ remembers facts in a local database

Phone     ──►  Twilio  ──►  bridge (/twilio/voice)  ──►  Media Stream (WebSocket)
                                                      ──►  gradbot session
              you (owner)  → your assistant (your voice + memory + tools)
              anyone else  → AI receptionist (takes a message)
```

A single service runs both the **bridge** (phone calls + API) and the **bot**
(Telegram). Data (your profile, voice id, memory, call history) lives in a small
SQLite database on disk (or Postgres for larger hosted setups).
