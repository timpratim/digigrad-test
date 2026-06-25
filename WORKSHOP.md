# Build Your Digital Clone — Complete Gradphone Workshop Guide

**Audience:** AI engineers who want to build, operate, and reason about a real phone-based voice agent.  
**Primary attendee outcome:** each participant registers, clones their own voice, chats with the clone, receives a real phone call from it, and understands the production architecture behind it.  
**Instructor outcome:** a repeatable, safety-conscious workshop that can run from one hosted instance or from individual developer deployments.  
**Last updated:** 2026-06-17.

---

## Content

Learn how to build and operate **gradphone**, a personal voice-agent system that speaks in the user’s cloned voice.

The workshop starts with the fastest hands-on path: register in Telegram, explicitly consent to voice cloning, create a Gradium clone from a short voice note, then make the clone call you on a real phone. From there, participants inspect the architecture: Telegram bot, FastAPI bridge, Twilio Media Streams, gradbot’s STT → LLM → TTS loop, per-tenant memory, web search, Gmail summaries, and inbound receptionist mode.

The teaching style is intentionally hands-on: every lesson ends with a concrete verification artifact, such as an active clone, a call room ID, a saved memory, a call result, or a dashboard/history entry. The workshop borrows the strongest patterns from recent AI Engineer-style workshops: setup first, clarify safety and scope early, keep slices thin and demoable, observe the system before modifying it, and debug with visible feedback loops.

### What you’ll learn

- How realtime voice agents are assembled from audio transport, turn detection, STT, LLM tool use, TTS, and telephony.
- How to safely create a personal voice clone with explicit user consent.
- How gradphone routes between three modes: personal assistant, outbound business caller, and inbound receptionist.
- How memory, web search, and Gmail summaries are exposed as voice-agent tools.
- How to deploy, test, observe, and troubleshoot a real Twilio-backed voice agent.
- How to extend the agent with a new tool or behavior without breaking spoken conversation quality.

### Who it’s for

Engineers comfortable with Python, APIs, environment variables, and reading application code. No voice-AI background is required for the hosted attendee path. The self-hosted path expects comfort with Twilio, Telegram bot setup, Render or local tunnels, and secret management.

---

## The workshop product

Participants build a **digital voice clone of themselves**:

1. The participant opens the Telegram bot and runs `/register`.
2. They send a clean voice note and explicitly confirm it is their own voice.
3. Gradium creates a per-tenant voice clone.
4. The bot can now answer typed messages or voice notes as that assistant.
5. The system can place a phone call to the participant with `/callme`.
6. With optional credentials, the phone assistant can summarize recent Gmail and answer current questions through web search.
7. With inbound enabled, the Twilio number can answer incoming calls as a receptionist and take a message.

The “wow” moment is hearing your own synthetic voice participate in a live phone call, while still understanding the operational reality behind it: telephony, latency, turn-taking, tool failures, consent, logs, quotas, and safety boundaries.

---

## Recommended formats

| Format | Best for | What attendees do |
|---|---|---|
| **90-minute expo lab** | High throughput, low setup risk | Use one hosted bot, register, clone, voice-note chat, queued `/callme`, inspect dashboard. |
| **3-hour workshop** | Default recommendation | Hosted path plus code walkthrough, memory/web/email tools, inbound receptionist demo, debugging exercise. |
| **4-hour builder workshop** | Engineers who will fork/deploy | Full 3-hour path plus Render or local deployment, Twilio webhook setup, and one small extension. |

The default 3-hour agenda is below. The hosted path keeps the live workshop from being dominated by account setup and telephony edge cases. The self-hosted deployment is included as an appendix and optional builder track.

---

## Research-backed teaching principles used here

Public AI Engineer workshop material follows a repeatable pattern: first get the repo running, then turn ambiguity into artifacts, then break work into thin vertical slices, and finally run/observe/debug an agent human-in-the-loop before trusting autonomy. This guide adapts that pattern to voice agents:

1. **Setup before concepts.** Voice systems have many external moving parts. Get everyone to one green checkpoint before deep architecture.
2. **Consent before cloning.** The first successful interaction must also model safe operation.
3. **One thin vertical slice first.** The first slice is: Telegram registration → voice clone → assistant reply. Only then add phone calls, tools, and inbound flows.
4. **Visible artifacts.** Each lesson leaves a receipt: clone ID, Telegram response, room ID, call result, memory fact, dashboard row, or log line.
5. **Observe before modifying.** Participants first watch a working voice loop, then inspect code and prompts, then make a small change.
6. **Debug with production signals.** Voice-agent workshops should teach latency, interruption, IVR, hold, silence, and tool failure as first-class engineering concerns.
7. **Use acceptance criteria.** Every exercise has a definition of done so assistants can help without open-ended drift.

---

## Architecture at a glance

```text
Telegram user
   │
   │  /register, voice note, text chat, /call, /callme, /web
   ▼
Telegram bot (src/gradphone/bot.py)
   │
   │  HTTP /dial, /tenants, /history, /calls/live  (Bearer BRIDGE_API_KEY)
   ▼
FastAPI bridge (src/gradphone/bridge.py)
   │
   │  Twilio REST outbound call + TwiML webhook
   ▼
Twilio Programmable Voice
   │
   │  <Connect><Stream> to wss://.../twilio/stream?room=...
   ▼
Media Streams WebSocket
   │
   │  μ-law 8 kHz phone audio ⇄ resampled PCM for gradbot
   ▼
gradbot session
   │
   ├─ STT via Gradium
   ├─ LLM via OpenAI-compatible endpoint
   ├─ tools: remember, recall, web_search, get_email_summary, take_message, save_business_result, DTMF, hang_up
   └─ TTS via Gradium voice clone

SQLite/Postgres data layer
   ├─ tenants
   ├─ calls/history
   └─ per-tenant durable memories
```

Mode selection happens at dial time:

| Mode | Entry point | Prompt | Tools | Primary demo |
|---|---|---|---|---|
| `assistant` | `/callme +number` | `build_assistant_prompt()` | memory, web search, Gmail summary, hangup | Your clone calls you. |
| `business` | `/call` guided flow or CLI | `build_business_prompt()` | DTMF, wait, save result, end call | Agent asks a business a constrained question. |
| `receptionist` | inbound Twilio webhook | `build_receptionist_prompt()` | take message, hangup | Agent answers your line and takes a message. |

---

## Workshop-day safety and scope

Use this section verbatim at the start of the workshop.

- Clone only **your own voice**. Do not upload another person’s voice, celebrity audio, podcast clips, or recordings where consent is unclear.
- The bot asks for explicit confirmation before cloning. Do not bypass this in a workshop build.
- Use your own phone number or an approved test number for phone calls.
- Keep outbound calling controlled. For public workshops, prefer `ALLOW_ARBITRARY_OUTBOUND=0` with an `OUTBOUND_ALLOWLIST`, or have the instructor queue calls.
- Do not ask the agent to obtain, disclose, or process financial, medical, legal, credential, or identity secrets.
- Do not commit `.env` files, voice samples, recordings, Gmail app passwords, Twilio tokens, Telegram bot tokens, or Gradium keys.
- Calls may be recorded by the application for debugging. Tell participants where recordings are stored and how to delete them.
- Follow local call-recording, consent, and telemarketing laws. A workshop demo is not a license to cold-call strangers.

---

## Instructor prep checklist

Complete this before attendees enter the room.

### 1. Provision the hosted instance

Recommended for AI Engineer-style sessions:

- One Render web service from `render.yaml` on at least the Starter plan.
- `GRADBOT_MAX_CONCURRENT=3` or lower if provider limits require it.
- `MAX_CALL_DURATION_SECONDS=180` for workshop safety.
- `WORKSHOP_CODE` set to a short code announced in-room.
- `DEFAULT_DAILY_QUOTA` set low enough to prevent accidental spend.
- `ENABLE_INBOUND=true` only when the instructor is ready for inbound tests.
- `TWILIO_MACHINE_DETECTION=Disable` for lower call setup complexity during demos.
- A stable public HTTPS/WSS URL. Prefer a named tunnel or hosted Render URL over a one-off tunnel.

### 2. Configure required secrets

Required for the core workshop:

```bash
TELEGRAM_BOT_TOKEN=...
GRADIUM_API_KEY=...
LLM_BASE_URL=...
LLM_MODEL=...
OPENAI_API_KEY=...        # or use GRADIUM_API_KEY if your endpoint accepts it
TWILIO_ACCOUNT_SID=...
TWILIO_AUTH_TOKEN=...
TWILIO_FROM_NUMBER=+...
PUBLIC_HTTP_URL=https://...
PUBLIC_WS_URL=wss://...
BRIDGE_API_KEY=...
```

Recommended optional secrets:

```bash
LINKUP_API_KEY=...          # live web search
GMAIL_ADDRESS=you@gmail.com # read-only Gmail summary demo
GMAIL_APP_PASSWORD=...      # 16-character app password; 2FA required
WORKSHOP_CODE=...
DATABASE_URL=...            # optional Postgres; SQLite works for single-host demos
```

### 3. Lock down outbound calling

For a public workshop, use one of these patterns:

**Strict queue:** instructor runs calls one at a time and attendees only call themselves.

```bash
ALLOW_ARBITRARY_OUTBOUND=0
OUTBOUND_ALLOWLIST=+15551234567,+33612345678
DEFAULT_DAILY_QUOTA=2
GRADBOT_MAX_CONCURRENT=1
```

**Small breakout room:** permit attendee phone numbers after registration, but keep business-call mode restricted.

```bash
DEFAULT_DAILY_QUOTA=3
GRADBOT_MAX_CONCURRENT=2
MAX_CALL_DURATION_SECONDS=180
```

### 4. Pre-warm the system

Run these in order before the room opens:

```bash
curl -fsS "$PUBLIC_HTTP_URL/healthz"
python -m gradphone.dial +15551234567 "Say hello and confirm audio works" --no-wait
```

Then manually verify:

- Telegram bot responds to `/start`.
- `/register <WORKSHOP_CODE>` creates a tenant.
- Voice clone works from a clean 20-second sample.
- `/callme <your phone>` creates a Twilio call and the phone audio is audible.
- `/history` and `/status` work.
- `/web` produces a dashboard magic link.
- Twilio webhook points to `https://<public-host>/twilio/voice`.
- Logs are visible in Render or the terminal.

### 5. Prepare the room

- Use a reliable hotspot or wired network for the instructor machine.
- Keep provider dashboards open: Render logs, Twilio call logs, Telegram bot chat, Gradium dashboard, and optional Linkup/Gmail status.
- Have a slide or QR code for the Telegram bot username and workshop code.
- Keep a visible call queue: name, phone number, status, room ID.
- Prepare two fallback demos: a recorded successful call and a local Telegram voice-note chat path.

---

## Default 3-hour agenda

| Time | Module | Outcome |
|---:|---|---|
| 0:00–0:10 | Kickoff, safety, and what we are building | Attendees know boundaries and success criteria. |
| 0:10–0:30 | Register and clone your voice | Everyone has a tenant and a Gradium clone or fallback default voice. |
| 0:30–0:50 | Telegram text/voice-note chat | Attendees see STT → LLM → TTS without phone complexity. |
| 0:50–1:15 | Personal phone call with `/callme` | Each attendee hears their clone over a real phone call, queued if needed. |
| 1:15–1:30 | Break + call queue overflow | Instructor resolves setup issues. |
| 1:30–1:55 | Architecture walkthrough | Attendees can trace Telegram → bridge → Twilio → gradbot → tools. |
| 1:55–2:20 | Memory, web search, and Gmail tools | Attendees understand tool surfaces and graceful failure. |
| 2:20–2:40 | Inbound receptionist and outbound business mode | Attendees see mode-specific prompts and tool constraints. |
| 2:40–2:55 | Debugging voice failures | Attendees diagnose logs, status, prompt issues, silence, latency, and IVR. |
| 2:55–3:00 | Wrap and extensions | Attendees leave with next steps and take-home exercises. |

---

# Lesson 1 — Kickoff, safety, and architecture preview

## Goal

Participants understand the product, the safety envelope, and the end-to-end shape of a realtime voice agent before touching the bot.

## Instructor talk track

“We are building a personal voice agent that can speak in your cloned voice. You will explicitly consent before cloning. The core loop is simple to describe but tricky to operate: audio comes from Telegram or Twilio, the system transcribes it, an LLM decides what to say or which tool to call, Gradium synthesizes the reply in your clone, and the result goes back as a voice note or phone audio.”

## Steps to complete

1. Show the architecture diagram.
2. Explain the three modes: assistant, business caller, receptionist.
3. Read the safety rules aloud.
4. Confirm every participant has:
   - Telegram installed.
   - A phone that can receive a call.
   - Headphones or a quiet enough environment for a 20-second sample.
   - The workshop code.

## Definition of done

Every attendee can answer:

- What audio is cloned?
- What phone number will be called first?
- What should they not upload or ask the agent to do?

---

# Lesson 2 — Register and create your voice clone

## Goal

Create a tenant and attach a voice clone.

## Attendee steps

1. Open the Telegram bot.
2. Send:

```text
/start
/register <workshop-code>
```

3. Share your phone number when prompted. This lets inbound mode recognize you later.
4. Record a clean voice note:
   - at least 20 seconds,
   - only your own voice,
   - no music,
   - no loud background noise,
   - normal speaking pace.
5. When the bot asks for consent, tap **Yes, clone my voice**.
6. Wait for the clone confirmation and copy the `uid` into your notes.
7. Send:

```text
/voice
```

## Sample voice script

Use this if attendees do not know what to say:

```text
Hi, this is my voice sample for the workshop. I am speaking clearly and naturally for about twenty seconds. I am giving consent to create a synthetic voice clone for this agent today. I will use it only for my own demo and testing.
```

## Definition of done

- `/whoami` says you are registered.
- `/voice` says a custom voice is active.
- You have a clone `uid`.

## Instructor notes

The Telegram bot intentionally treats an audio message differently depending on state. If a tenant has no clone, a voice/audio message becomes a pending clone sample. If a clone exists, a voice note becomes a voice-chat turn. To re-clone, attendees use `/clear_voice` and send a fresh sample.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| “Run /register first” | Tenant not created | Re-run `/register <code>`. |
| Consent prompt not shown | Audio was not received as voice/audio | Send a Telegram voice note or supported audio file. |
| Clone fails | Sample too short/noisy or provider error | Record a new 20–30 second clean sample. |
| `/voice` shows no clone | Clone did not persist | Check logs around `clone_from_bytes`; retry. |

---

# Lesson 3 — Talk to your clone in Telegram

## Goal

Exercise the voice loop without telephony: Telegram voice note → Gradium STT → LLM with memory → Gradium TTS → Telegram voice reply.

## Attendee steps

1. Send a text message:

```text
My favorite coffee drink is an oat milk cappuccino. Please remember that.
```

2. Confirm the assistant replies in text.
3. Send a voice note:

```text
What do you remember about my coffee order?
```

4. Listen to the voice reply.
5. Send:

```text
I usually prefer morning calls, before 10 AM.
```

6. Ask:

```text
What preferences do you know about me?
```

## Definition of done

- You received at least one text reply.
- You received at least one voice reply in your clone voice.
- The assistant remembered at least one durable fact or acknowledged learning it.

## Code tour

- `src/gradphone/bot.py` handles Telegram registration, cloning, text chat, and voice-note chat.
- `src/gradphone/voice_chat.py` converts Telegram OGG/Opus audio to PCM, runs Gradium STT, calls the LLM, synthesizes with the tenant voice ID, and converts the result back to OGG/Opus.
- `src/gradphone/memory.py` stores per-tenant durable facts and renders a compact memory digest.

## Teaching point

This is the first vertical slice. It proves identity, voice, LLM, TTS, and memory before adding phone latency, Twilio webhooks, and PSTN audio formats.

---

# Lesson 4 — Receive a phone call from your clone

## Goal

Run the personal assistant mode over a real phone call.

## Attendee steps

1. In Telegram, send:

```text
/callme +15551234567
```

Use your own E.164 number.

2. Answer the phone.
3. Try a short personal-assistant conversation:

```text
What do you remember about me?
```

4. Try one optional tool if configured:

```text
Summarize my emails this week.
```

or:

```text
Look up the latest AI Engineer World's Fair schedule.
```

5. End naturally:

```text
Thanks, that’s all. Goodbye.
```

6. Back in Telegram, run:

```text
/history
/status
```

## Definition of done

- You received a phone call.
- The call spoke with your clone or the fallback voice.
- `/history` shows a completed or attempted call.
- You saw a call room ID.

## Instructor queue mode

If concurrency is capped, collect names and numbers in a visible queue. Ask attendees to send `/callme` only when called on. This avoids provider throttling and makes failures diagnosable.

## What is happening under the hood

1. Telegram bot calls `dial(..., mode="assistant")`.
2. Bridge creates a room and Twilio outbound call.
3. Twilio asks `/twilio/voice` for TwiML.
4. Bridge returns `<Connect><Stream>` pointing to `/twilio/stream?room=...`.
5. The WebSocket streams phone audio to the bridge.
6. Bridge resamples audio between Twilio’s 8 kHz μ-law and gradbot’s PCM expectations.
7. `build_assistant_prompt()` selects the assistant prompt and tools.
8. The agent speaks first, handles memory/tools, and can hang up.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| No call arrives | Twilio credentials, from number, destination formatting, quota | Verify E.164 number, Twilio logs, Render logs. |
| Call connects but no audio | WebSocket URL, public WSS, resampling, gradbot startup | Check `/twilio/stream` logs and bridge exceptions. |
| Twilio webhook 403 | Public URL changed or signature mismatch | Update `PUBLIC_HTTP_URL`, `PUBLIC_WS_URL`, and Twilio webhook. |
| Agent talks over user | Turn detection or barge-in settings | Inspect silence/flush settings and barge-in logs. |
| Tool says email not configured | Missing Gmail app password | Set `GMAIL_ADDRESS` and `GMAIL_APP_PASSWORD`, then redeploy. |

---

# Lesson 5 — Inspect memory, web search, and Gmail tools

## Goal

Understand how a personal voice agent becomes useful without becoming unsafe or overbroad.

## Instructor demo

Ask the assistant, on a phone call or in text chat where configured:

```text
Remember that I am preparing a voice-agent workshop for AI Engineer.
```

Then:

```text
What do you remember about my workshop?
```

Then, if Linkup is configured:

```text
Search the web for the current AI Engineer World's Fair workshop day.
```

Then, if Gmail is configured:

```text
Summarize my emails from the last seven days.
```

## Code tour

- `memory.py`: small durable-fact store with duplicate guard, search, digest rendering, and post-call extraction.
- `business_agent.py`: assistant prompt lists memory, email, web search, and hangup behaviors.
- `bridge.py`: tool handlers execute memory recall, web search, Gmail fetch, message-taking, DTMF, and hangup.
- `email_inbox.py`: read-only Gmail IMAP integration using `GMAIL_ADDRESS` and `GMAIL_APP_PASSWORD`.
- `websearch.py`: Linkup-backed web search, when `LINKUP_API_KEY` is set.

## Discussion prompts

- What should be remembered permanently, and what should stay transient?
- Which tools are safe in a phone call without extra confirmation?
- How should the agent phrase a tool failure out loud?
- Why is “I’ll send that later” a bad behavior if the system cannot actually do it?

## Definition of done

Participants can identify which file owns:

- memory storage,
- assistant prompt behavior,
- Gmail summaries,
- web search,
- tool dispatch.

---

# Lesson 6 — Inbound receptionist mode

## Goal

Show the difference between “my assistant talking to me” and “my assistant answering for me.”

## Instructor setup

Inbound requires:

```bash
ENABLE_INBOUND=true
```

Twilio Voice webhook:

```text
https://<public-host>/twilio/voice
```

For public workshops, demo inbound with the instructor phone first. Attendees should not all call the shared number at once.

## Demo script

1. Instructor calls the Twilio number from a registered phone.
2. The system recognizes the owner number and can route to assistant behavior.
3. Another participant calls the same Twilio number.
4. The receptionist answers as the owner’s personal assistant, asks who is calling and why, takes a message, and hangs up.
5. The owner receives the message in Telegram.

## Teaching point

Receptionist mode deliberately has a smaller tool surface than assistant mode. It should not expose private memory, Gmail, or broad tools to random callers. It gathers a message and ends politely.

## Definition of done

- Participants can explain why the receptionist prompt has fewer tools.
- Instructor shows one delivered message.

---

# Lesson 7 — Outbound business-call mode

## Goal

Demonstrate a constrained, goal-directed voice agent that calls a business, handles IVR/hold/silence, and saves a structured result.

## Safety setup

Use only approved test numbers or businesses where the call is appropriate. In a workshop, a fake/local test line or instructor-controlled destination is best.

## Attendee path

In Telegram:

```text
/call
```

Then answer prompts:

```text
To: +15551234567
Task: Ask whether they are open after 6 PM today.
Language: EN
Confirm: Place call
```

or via CLI:

```bash
python -m gradphone.dial +15551234567 "Ask whether they are open after 6 PM today" --language en --business "Test Business"
```

## Code tour

- `BusinessCallSpec` captures task, language, business name, destination, booking permission, and mode.
- `build_business_prompt()` is intentionally narrow and explicit about voice behavior.
- Business tools include `press_dtmf`, `wait_silently`, `save_business_result`, and `end_business_call`.
- `/result/<room>` and `/history` expose structured call outcomes.

## Teaching point

Voice-agent prompts need different constraints than chat prompts. Anything the model emits is spoken. Parenthetical notes, stage directions, and “thinking” text become audio. The business prompt therefore repeatedly states that silence means empty output, not narration.

## Definition of done

- One business-mode call produces a structured result.
- Participants can explain the IVR rule, hold rule, listening rule, and result rule at a high level.

---

# Lesson 8 — Debugging realtime voice failures

## Goal

Teach participants to debug a voice agent using system signals instead of guessing.

## Debug map

| Layer | What to check | Typical failure |
|---|---|---|
| Telegram | Bot replies, `/whoami`, `/voice` | Registration or tenant lookup failed. |
| Clone | Voice `uid`, Gradium logs | Bad sample, missing API key, provider quota. |
| Bridge | Render logs, `/healthz`, `/diagnostics` | Missing env, DB init, auth, public URL mismatch. |
| Twilio webhook | Twilio call log, signature errors | Wrong webhook URL, stale tunnel, 403. |
| WebSocket | `/twilio/stream` logs | Public WSS unreachable, network, room missing. |
| Audio | STT transcripts, recording files | μ-law/PCM mismatch, silence threshold, noisy input. |
| LLM | prompt/mode/tool set | Wrong mode, tool call loop, verbose spoken output. |
| Tools | web/Gmail/memory errors | Missing API key, timeout, credential failure. |
| Call lifecycle | `/status`, `/history`, room state | Hangup not called, max duration, semaphore cap. |

## Instructor exercise

Choose one controlled failure:

1. Temporarily unset `LINKUP_API_KEY`, then ask a current-events question.
2. Use an unregistered Telegram user and run `/callme`.
3. Use an invalid E.164 number.
4. Set `ENABLE_INBOUND=false`, then discuss what inbound should do.

Have attendees fill out:

```text
Observed symptom:
Expected layer:
Evidence:
Fix:
Verification:
```

## Definition of done

Participants can map a symptom to a layer and name the next log or endpoint they would inspect.

---

# Lesson 9 — Extension exercise: add one safe personal tool

## Goal

Participants practice modifying the system using a thin vertical slice.

## Suggested extension

Add a `get_local_time` tool for assistant mode.

### Acceptance criteria

- The assistant prompt lists the new capability.
- The tool schema is available only in assistant mode.
- The handler returns a concise result.
- A phone call or text chat can trigger the tool.
- If the tool fails, the spoken response is short and honest.

### Implementation hints

1. Add a tool definition near the assistant tool definitions in `bridge.py`.
2. Include it in `_assistant_tool_defs()`.
3. Add a branch in `_handle_tool_call()`.
4. Mention the tool in `build_assistant_prompt()`.
5. Test with:

```text
What time is it in Paris right now?
```

### Why this extension works

It is small, demonstrable, and crosses the actual production path: prompt → tool schema → tool dispatch → spoken answer. It does not require new provider accounts or sensitive data.

---

## Self-hosted builder path

Use this for a 4-hour workshop or take-home track.

### Local prerequisites

- Python 3.12.
- `ffmpeg` on PATH.
- A Telegram bot token from BotFather.
- Gradium API key.
- OpenAI-compatible LLM endpoint and model.
- Twilio paid account and voice-capable number.
- Public HTTPS/WSS tunnel such as cloudflared or ngrok.

### Local setup

```bash
git clone <repo-url> gradphone-aie
cd gradphone-aie
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env
```

Edit `.env` with required secrets.

Run bridge:

```bash
uvicorn gradphone.bridge:app --host 0.0.0.0 --port 8082
```

Run Telegram bot in another terminal:

```bash
python -m gradphone.bot
```

Expose the bridge:

```bash
cloudflared tunnel --url http://127.0.0.1:8082
```

Set:

```bash
PUBLIC_HTTP_URL=https://<your-tunnel-host>
PUBLIC_WS_URL=wss://<your-tunnel-host>
GRADBOT_BRIDGE_URL=http://127.0.0.1:8082
```

Configure Twilio Voice webhook:

```text
https://<your-tunnel-host>/twilio/voice
```

Verify:

```bash
curl -fsS http://127.0.0.1:8082/healthz
python -m gradphone.dial +15551234567 "Ask if audio works" --no-wait
```

### Render deployment path

Use the Blueprint from `render.yaml`. After deploy:

1. Set all secrets in Render.
2. Confirm `/healthz` returns success.
3. Set Twilio webhook to `https://<render-host>/twilio/voice`.
4. Set `PUBLIC_HTTP_URL=https://<render-host>` and `PUBLIC_WS_URL=wss://<render-host>`.
5. Open the Telegram bot and run the core workshop path.

---

## Instructor run-of-show script

### Opening

“Today you will build a digital clone that can speak in your voice. We will do this safely: you will only clone your own voice, you will consent explicitly, and we will control outbound calls. The system is not magic. It is a realtime engineering pipeline with audio transport, speech recognition, an LLM, tool calls, speech synthesis, and lots of failure modes.”

### Before clone

“Use a quiet voice note. If your sample has room noise or music, your clone and transcription quality will suffer. The clone should be your own voice only.”

### Before phone calls

“Calls are queued because realtime voice systems are bottlenecked by provider concurrency and phone setup. If your call fails, that is useful data. We’ll debug the layer, not blame the model.”

### Before code walkthrough

“The product works because modes are constrained. Assistant mode has personal tools. Receptionist mode does not. Business-call mode is narrow and result-oriented. Voice agents fail when we treat every call like open-ended chat.”

### Closing

“You now have a working personal voice-agent vertical slice. The next engineering step is not adding every tool. It is making the loop safer, more observable, more testable, and more explicit about when it should act.”

---

## Participant worksheet

```text
Telegram username:
Tenant ID:
Voice clone UID:
Phone number used for /callme:
First call room ID:
One memory I taught the assistant:
One tool I tested:
One failure or surprise I observed:
One extension I would build next:
```

---

## Operator dashboard checklist

During the session, monitor:

- Number of registered tenants.
- Number of active calls.
- Daily quota consumption.
- Failed clone attempts.
- Twilio call errors.
- Provider rate-limit or concurrency errors.
- Long-running calls near `MAX_CALL_DURATION_SECONDS`.
- Any unexpected outbound destination.

After the session:

- Disable or rotate `WORKSHOP_CODE`.
- Consider disabling inbound if not needed.
- Review call recordings and retention policy.
- Rotate any shared secrets used during the demo.
- Reduce Twilio/Render/Gradium quotas if the environment stays online.

---

## Code map

| File | Why it matters in the workshop |
|---|---|
| `README.md` | Quick product overview, setup, deployment, and command list. |
| `.env.example` | Complete configuration surface and safe defaults. |
| `render.yaml` | Hosted workshop deployment. |
| `src/gradphone/bot.py` | Telegram registration, cloning consent, `/call`, `/callme`, `/voice`, `/web`, text/voice chat. |
| `src/gradphone/bridge.py` | FastAPI bridge, Twilio webhooks, Media Streams, gradbot session, tools, call state. |
| `src/gradphone/business_agent.py` | Mode-specific prompts: business, assistant, receptionist. |
| `src/gradphone/voice_chat.py` | Telegram voice-note STT/LLM/TTS path. |
| `src/gradphone/memory.py` | Per-tenant durable memory. |
| `src/gradphone/email_inbox.py` | Read-only Gmail summary integration. |
| `src/gradphone/dial.py` | CLI dispatcher for outbound calls and result polling. |
| `src/gradphone/tenants.py` | Tenant registration, quotas, phone identity, voice IDs. |
| `src/gradphone/web.py` | Dashboard UI and operator/tenant visibility. |

---

## Troubleshooting appendix

### “The agent says parenthetical text out loud”

Fix the prompt or model output. In voice, every emitted token may become speech. Never rely on a renderer stripping stage directions. Business and receptionist prompts already include strict rules against this.

### “The bot is replying in text but not voice”

Check whether the tenant has a `voice_id`. If no clone exists, voice/audio messages are treated as clone samples. Run `/voice`.

### “The phone call starts late”

Expected causes include Twilio call setup, WebSocket connection, first STT frame, LLM first token, and TTS generation. The business prompt uses a cached short opener pattern to reduce perceived first-word latency.

### “The agent interrupts lists or prices”

Inspect turn detection and prompt behavior. Business mode has explicit listening/list rules for prices, lists, and unfinished utterances. Assistant mode may need shorter replies and better silence handling for the specific scenario.

### “The system works locally but not with Twilio”

Most common causes:

- stale tunnel URL,
- `PUBLIC_HTTP_URL` and `PUBLIC_WS_URL` mismatch,
- Twilio webhook points to old host,
- WebSocket path unreachable,
- signature validation fails because host/proxy changed.

### “Gmail summary fails”

The integration uses read-only Gmail over IMAP. Confirm:

```bash
GMAIL_ADDRESS=...
GMAIL_APP_PASSWORD=...
```

The Google account needs 2FA enabled to create an app password. The agent should gracefully say email is not configured rather than crash the call.

### “Calls keep piling up”

Lower concurrency and call length:

```bash
GRADBOT_MAX_CONCURRENT=1
MAX_CALL_DURATION_SECONDS=120
DEFAULT_DAILY_QUOTA=2
```

Use `/status` to see in-flight calls and Twilio logs to terminate stuck calls if necessary.

---

## Research notes and source links

The workshop design was informed by the following public materials and patterns:

- AI Engineer World's Fair 2026 emphasizes a workshop-heavy first day, voice/realtime AI, personal agents, memory, evals, and agentic engineering tracks.
- The AI Hero AI Engineer Workshop 2026 model uses a compact course landing page, explicit “what you’ll learn,” setup-first lessons, artifact-driven exercises, tracer bullets, human-in-the-loop observation, and feedback loops.
- OpenAI voice-agent guidance frames the main architecture choice as direct speech-to-speech sessions versus chained STT → agent workflow → TTS pipelines.
- Twilio Media Streams documentation clarifies why gradphone uses `<Connect><Stream>` and a public secure WebSocket for bidirectional phone audio.
- LiveKit Agents, Pipecat, Vapi, ElevenLabs, and Deepgram docs all converge on the same production primitives: realtime media transport, STT, LLM orchestration, TTS, turn detection, tool calling, telephony, testing, and observability.
- Recent realtime voice-agent tutorials and AI Engineer session titles reinforce the core failure modes taught here: latency, barge-in, IVR, tool timing, memory, debugging spoken output, and when an agent should act versus wait.

Useful source URLs:

- AI Engineer: `https://www.ai.engineer/`
- AI Engineer World's Fair schedule: `https://www.ai.engineer/worldsfair/schedule`
- AI Hero workshop reference: `https://www.aihero.dev/ai-engineer-workshop-2026~dwnll`
- OpenAI voice agents: `https://platform.openai.com/docs/guides/voice-agents`
- OpenAI realtime/audio: `https://platform.openai.com/docs/guides/realtime`
- Twilio Media Streams: `https://www.twilio.com/docs/voice/media-streams`
- Twilio `<Stream>` TwiML: `https://www.twilio.com/docs/voice/twiml/stream`
- LiveKit Agents: `https://docs.livekit.io/agents/`
- Pipecat: `https://docs.pipecat.ai/`
- Vapi docs: `https://docs.vapi.ai/`
- ElevenLabs ElevenAgents: `https://elevenlabs.io/docs/eleven-agents/overview`
- Deepgram Voice Agent: `https://developers.deepgram.com/docs/voice-agent`
