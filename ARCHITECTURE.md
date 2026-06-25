# gradphone — Digital Clone Architecture

Status: **design, for review.** Describes the voice-first redesign and the
memory/identity model. Implementation is phased (§7); nothing here is built
yet unless noted.

## 1. The shift: voice-first, Telegram-as-log

Today the agent is command-first: you type `/call` / `/callme` in Telegram and
it acts. The redesign inverts that:

- **Primary interface = the phone.** You call one number and *talk*. Who you
  are, what you want, and what the agent remembers all flow through speech.
- **Telegram = the activity log.** Not a command bar — a feed of what the
  agent did: call summaries, taken messages, actions awaiting your approval,
  searchable history. Slash commands remain as a convenience, not the path.

## 2. Identity by caller ID

One shared number; the caller's number decides the experience:

```
incoming call → /twilio/voice
      │  look up tenant by From number
      ├── known tenant  → ASSISTANT mode
      │                    • answers in that tenant's cloned voice
      │                    • loads that tenant's memory (Honcho peer)
      │                    • full tools: recall, email, (later) actions
      └── unknown caller → RECEPTIONIST mode
                           • screens, answers basics, takes a message
                           • relays to the owner; no persistent profiling
```

This is what makes one number multi-tenant: attendee A calls and hears A's
voice with A's memory; attendee B hears B's. Requires a **phone → tenant**
map, so `/register` must capture the user's phone number (new `tenants.phone`
column; Telegram "share contact" button or typed E.164).

`/callme` survives as the *proactive* direction (agent calls you), but calling
in is now the default way to reach your clone.

## 3. Knowing the person — three layers

A clone needs to **sound like you**, **talk like you**, **know what you know**,
and **know what's happening now**. We separate these (the Hermes lesson — keep
persona, memory, and live context distinct):

| Layer | What | Source of truth | Seeded by |
|---|---|---|---|
| **Voice** | how you sound | Gradium clone (`tenants.voice_id`) | voice note onboarding |
| **Persona** | how you talk, your role/style | Honcho peer representation + a short persona note | onboarding interview |
| **Memory** | facts, people, past calls | **Honcho** (peers/sessions/messages → reasoning) | accumulates per call |
| **Live context** | email, calendar, contacts | connectors (`email_inbox.py`, …) | on demand |

Operational data (tenants, call rows, quotas, recordings) stays in our SQLite.
**Honcho is the knowledge/identity layer, not the operational DB.**

## 4. Memory via Honcho

We use [Honcho](https://honcho.dev) for user modeling instead of building our
own facts table + FTS5. Honcho ingests conversation and runs reasoning to build
a queryable representation of each person ("theory of mind"), which we read back
as natural-language context.

### 4.1 Mapping our domain onto Honcho

| Honcho concept | gradphone |
|---|---|
| **Workspace** | one per deployment (`gradphone`) |
| **Peer** | a tenant (the human). `peer_id = tenant:<id>`. The clone/assistant is a second peer (`assistant:<id>`) |
| **Session** | one phone call (`session_id = <room>`) |
| **Message** | one transcript turn (caller speech / agent speech), attributed to its peer |

Strangers in receptionist mode are **not** made into peers — we don't build
profiles of random callers (privacy). Only registered tenants get a peer.

### 4.2 Write path (during/after a call)

Each finalized transcript turn is appended to the call's Honcho session as a
message, attributed to the right peer. Cheap and non-blocking — runs off the
audio path (like the existing `asyncio.to_thread` email fetch). On call end the
session closes and Honcho's background reasoning updates the peer representation.
We already produce these turns (`_append_transcript`); we tee them to Honcho.

### 4.3 Read path (the payoff)

- **At call start**, after identifying the peer, query Honcho's **dialectic
  `chat`** endpoint — e.g. *"Summarize what you know about this person that's
  relevant to acting as their personal assistant on a phone call."* — and inject
  the answer into the system prompt as a `WHAT YOU KNOW ABOUT THE CALLER` block.
- **Mid-call**, a `recall` tool lets the agent ask Honcho specific questions
  ("what's my dentist's name?", "did anyone call about the lease?") and speak the
  answer. This is the cross-session recall Hermes gets from FTS5 — Honcho does it
  via reasoning instead.

```
call start ─► identify peer ─► honcho.chat("what should I know…") ─► prompt
   │
   ├─ turn ─► append message to session ─┐  (async, non-blocking)
   │                                     │
   ├─ agent needs a fact ─► recall tool ─► honcho.chat("…") ─► spoken answer
   │
call end ─► close session ─► Honcho reasons ─► representation deepens
                                  │
                                  └─► post-call summary pushed to Telegram
```

### 4.4 SDK / config (verify signatures against Honcho SDK reference)

```python
# pip install honcho-ai   (confirm package + method names at build time)
from honcho import Honcho
honcho = Honcho(api_key=os.environ["HONCHO_API_KEY"],
                workspace=os.environ.get("HONCHO_WORKSPACE", "gradphone"),
                base_url=os.environ.get("HONCHO_BASE_URL"))  # unset = managed

peer    = honcho.peer(f"tenant:{tenant_id}")
session = honcho.session(room)
session.add_messages([...])          # write transcript turns
context = peer.chat("what should I know about this caller?")  # dialectic read
```

New env: `HONCHO_API_KEY`, `HONCHO_WORKSPACE`, `HONCHO_BASE_URL`
(blank = managed at app.honcho.dev; set for self-host).

## 5. The onboarding interview (persona + memory seed)

Voice-first onboarding: instead of typing a bio, **the clone calls you and
interviews you** — a scripted ~5-question mode ("tell me about your work", "how
do you like to communicate", "who calls you most and why"). Answers stream into
Honcho as the peer's first session, bootstrapping the representation, and a
short persona note is distilled for the prompt. One call and the clone knows you.
This is a new call mode (`interview`) alongside business/assistant/receptionist.

## 6. Telegram as activity log

- After every call, the bridge pushes a summary to the tenant's Telegram chat
  (generalize the existing `_notify_operator` to per-tenant `telegram_id`):
  *"☎️ 2:14pm — you asked me to call the dentist; booked Thu 3pm."*
- Message relays (receptionist) and, later, action-approval prompts land here.
- `/history` stays and gets richer; commands de-emphasized in the UX.

## 7. Build phases

1. **Caller-ID identity** — `tenants.phone` + capture at `/register`; inbound
   routes owner→assistant / stranger→receptionist; assistant uses the tenant's
   voice + peer.
2. **Honcho integration** — client + peer/session/message plumbing; dialectic
   context injection at call start; `recall` tool. Degrades gracefully if
   `HONCHO_API_KEY` is unset (no memory, agent still works).
3. **Onboarding interview mode** — scripted interview call → seeds Honcho +
   persona.
4. **Telegram-as-log** — per-tenant post-call summaries; reframe the bot.
5. **Action layer** — Telegram-confirm primitive (draft → ✅ → execute) for
   email send, bookings, etc.

## 8. Open decisions / risks

- **Honcho managed vs self-host.** Managed is fastest; self-host keeps personal
  conversation data in-house. Personal call content leaving to a third party is
  a privacy consideration worth a deliberate choice.
- **Graceful degradation.** Honcho must be optional — if it's down or unset, the
  agent still completes calls without memory. (Non-negotiable for demo day.)
- **Latency.** The call-start dialectic query is on the critical path before the
  agent greets; needs a tight timeout + fallback to "no context."
- **Phone capture UX.** Telegram share-contact vs typed number; numbers must be
  normalized to E.164 to match Twilio's `From`.
- **Stranger privacy.** Receptionist callers are not profiled; confirm that's the
  policy.
