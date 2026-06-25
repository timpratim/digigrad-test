"""Telegram trigger for gradphone — self-serve registration + dashboard link.

Run:
    python -m gradphone.bot

Required env:
    TELEGRAM_BOT_TOKEN     — from @BotFather
    GRADBOT_BRIDGE_URL     — default http://127.0.0.1:8082
    BRIDGE_API_KEY         — same value the bridge enforces on /dial
                             and used to sign magic-link tokens.
    PUBLIC_HTTP_URL        — needed for /web to generate a public URL.

Commands:
    /whoami    — show Telegram ID + registration state
    /register  — self-serve: anyone can register; rate-limited per tenant
    /call      — guided call flow
    /history   — last 10 calls placed by you
    /status    — currently in-flight calls (yours only)
    /web       — DM a magic link to the web dashboard (5-min expiry)
    /translate — real-time voice translation in your cloned voice
    /cancel    — abort a /call mid-way
"""

from __future__ import annotations

import asyncio
import html
import logging
import os
from typing import Optional

import aiohttp
from dotenv import load_dotenv
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
)
from telegram.ext import (
    Application,
    ApplicationHandlerStop,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    TypeHandler,
    filters,
)
from telegram import Update as _Update

from . import tenants as _tenants_db
from . import translate as _translate
from . import voice_chat as _voice_chat
from . import voices as _voices
from .dial import _auth_headers, _format_result, dial, wait_for_result
from .sessions import make_magic_token

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("gradphone.bot")

ASK_TO, ASK_TASK, ASK_LANG, CONFIRM = range(4)
LANGUAGES = ["en", "fr", "pt"]
MAX_HISTORY_DISPLAYED = 10


def _bridge_url() -> str:
    return os.environ.get("GRADBOT_BRIDGE_URL", "http://127.0.0.1:8082").rstrip("/")


async def _fetch_tenant(telegram_id: int) -> Optional[dict]:
    """Look up the tenant via the bridge's /tenants/{telegram_id} endpoint."""
    async with aiohttp.ClientSession() as sess:
        try:
            async with sess.get(
                f"{_bridge_url()}/tenants/{telegram_id}",
                headers=_auth_headers(),
                timeout=aiohttp.ClientTimeout(total=10),
            ) as r:
                data = await r.json()
        except aiohttp.ClientError as e:
            log.warning("tenant lookup failed: %s", e)
            return None
    if not data.get("ok"):
        return None
    return data.get("tenant")


# ─── Command handlers ────────────────────────────────────

async def start(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    tenant = await _fetch_tenant(user.id) if user else None
    if tenant:
        await update.message.reply_text(
            f"Welcome back, {tenant['name']}.\n\n"
            "/call to place a call.\n"
            "/web for the web dashboard.\n"
            "/history /status /whoami /cancel"
        )
        return
    await update.message.reply_text(
        "Hi — I'm gradphone, an outbound voice AI agent.\n\n"
        "Send /register to create your account, then /call to place your "
        "first call.\n\n"
        "/whoami shows your Telegram ID."
    )


async def whoami(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    tenant = await _fetch_tenant(user.id) if user else None
    lines = [
        f"Telegram ID: {user.id if user else 'unknown'}",
        f"Username:    @{user.username if user and user.username else '-'}",
        f"Registered:  {'yes (tenant_id=' + str(tenant['id']) + ')' if tenant else 'no — send /register'}",
    ]
    await update.message.reply_text("\n".join(lines))


async def register(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """One-time owner setup.

    Access is already restricted to the owner by the gatekeeper
    (ALLOWED_TELEGRAM_IDS), so this just creates the owner's profile row the
    first time and is idempotent thereafter.
    """
    user = update.effective_user
    if user is None:
        return
    existing = await _fetch_tenant(user.id)
    if existing:
        await update.message.reply_text(
            f"You're already registered (tenant_id={existing['id']}, "
            f"name: {existing['name']}). Send /call to place a call, "
            "or /web for the dashboard."
        )
        return
    name = user.full_name or user.username or f"user_{user.id}"
    async with aiohttp.ClientSession() as sess:
        async with sess.post(
            f"{_bridge_url()}/tenants",
            json={"telegram_id": user.id, "name": name},
            headers=_auth_headers(),
            timeout=aiohttp.ClientTimeout(total=10),
        ) as r:
            data = await r.json()
    if not data.get("ok"):
        await update.message.reply_text(f"Registration failed: {data.get('error', 'unknown')}")
        return
    contact_kb = ReplyKeyboardMarkup(
        [[KeyboardButton("📱 Share my number", request_contact=True)]],
        resize_keyboard=True, one_time_keyboard=True,
    )
    await update.message.reply_text(
        f"Registered as <code>{html.escape(name)}</code> "
        f"(tenant_id={data.get('tenant_id')}).\n\n"
        "Share your number so that when you call the agent it recognizes you "
        "and answers as your personal assistant (with your voice + memory). "
        "Tap below, or send a voice note to clone your voice.",
        parse_mode="HTML",
        reply_markup=contact_kb,
    )


async def save_contact(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    """Store the tenant's phone (shared via Telegram contact) for caller-ID
    identity on inbound calls. Only accepts the user's OWN contact."""
    user = update.effective_user
    contact = update.message.contact if update.message else None
    if user is None or contact is None:
        return
    if contact.user_id and contact.user_id != user.id:
        await update.message.reply_text(
            "Please share your own number, not someone else's.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return
    tenant = await _fetch_tenant(user.id)
    if not tenant:
        await update.message.reply_text("Run /register first.", reply_markup=ReplyKeyboardRemove())
        return
    await _tenants_db.set_tenant_phone(int(tenant["id"]), contact.phone_number)
    await update.message.reply_text(
        "Got it — when you call the agent from this number, it'll greet you as "
        "your own assistant. Send a voice note next to clone your voice.",
        reply_markup=ReplyKeyboardRemove(),
    )


async def web(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a signed magic link that logs the tenant into the web dashboard."""
    user = update.effective_user
    if user is None:
        return
    tenant = await _fetch_tenant(user.id)
    if not tenant:
        await update.message.reply_text("Run /register first.")
        return
    public = os.environ.get("PUBLIC_HTTP_URL", "").rstrip("/")
    if not public:
        await update.message.reply_text(
            "Web dashboard isn't reachable — PUBLIC_HTTP_URL not set on the bridge."
        )
        return
    token = make_magic_token(int(tenant["id"]))
    link = f"{public}/ui/auth?token={token}"
    await update.message.reply_text(
        "Open this link to access your dashboard:\n"
        f"{link}\n\n"
        "Valid for 5 minutes. Once signed in, the session lasts 7 days.",
        disable_web_page_preview=True,
    )


async def call_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    tenant = await _fetch_tenant(user.id) if user else None
    if not tenant:
        await update.message.reply_text("Run /register first.")
        return ConversationHandler.END
    if not tenant.get("is_active", 1):
        await update.message.reply_text(
            "Your account is inactive. Contact the operator."
        )
        return ConversationHandler.END
    context.user_data.clear()
    context.user_data["tenant_id"] = tenant["id"]
    await update.message.reply_text(
        "What number should I call? E.164 format (e.g. +33144581010)."
    )
    return ASK_TO


async def got_to(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw = (update.message.text or "").strip()
    digits = "".join(ch for ch in raw if ch.isdigit())
    if len(digits) < 7:
        await update.message.reply_text("That doesn't look like a phone number. Try again or /cancel.")
        return ASK_TO
    context.user_data["to"] = "+" + digits
    await update.message.reply_text(
        "Got it. What should I ask or do on the call? "
        "Be specific — the agent will follow these instructions verbatim."
    )
    return ASK_TASK


async def got_task(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    task = (update.message.text or "").strip()
    if len(task) < 5:
        await update.message.reply_text("Task too short. Give me a real instruction or /cancel.")
        return ASK_TASK
    context.user_data["task"] = task
    keyboard = [[InlineKeyboardButton(code.upper(), callback_data=f"lang:{code}") for code in LANGUAGES]]
    await update.message.reply_text(
        "Language for the call?",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return ASK_LANG


async def got_language(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    code = query.data.split(":", 1)[1]
    if code not in LANGUAGES:
        await query.edit_message_text("Unknown language. /call to start over.")
        return ConversationHandler.END
    context.user_data["language"] = code
    to = context.user_data["to"]
    task = context.user_data["task"]
    keyboard = [[
        InlineKeyboardButton("Place call", callback_data="confirm:yes"),
        InlineKeyboardButton("Cancel", callback_data="confirm:no"),
    ]]
    await query.edit_message_text(
        f"Ready to call:\n• To: {to}\n• Language: {code}\n• Task: {task}",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return CONFIRM


async def confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    if query.data != "confirm:yes":
        await query.edit_message_text("Cancelled.")
        return ConversationHandler.END

    to = context.user_data["to"]
    task = context.user_data["task"]
    language = context.user_data["language"]
    tenant_id = context.user_data["tenant_id"]
    await query.edit_message_text(f"Dialing {to}…")

    out = await dial(to=to, reason=task, language=language, tenant_id=tenant_id)
    if out.startswith("Error"):
        await query.message.reply_text(out)
        return ConversationHandler.END

    room = out
    await query.message.reply_text(
        f"Call placed (room: <code>{html.escape(room)}</code>). Waiting for result…",
        parse_mode="HTML",
    )
    data = await wait_for_result(room)
    formatted = _format_result(data)
    await query.message.reply_text(
        f"<pre>{html.escape(formatted)}</pre>",
        parse_mode="HTML",
    )
    return ConversationHandler.END


async def cancel(update: Update, _: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Cancelled.")
    return ConversationHandler.END


# Background tasks must be referenced or asyncio may garbage-collect them
# mid-flight. /callme spawns a poller per call; keep a strong ref until done.
_BG_TASKS: set[asyncio.Task] = set()

# A /callme call can't outlive the bridge's MAX_CALL_DURATION_SECONDS (180 for
# the workshop). 220s comfortably covers a full-length connected call plus the
# ring/no-answer window, so we always see a terminal /result.
CALLME_RESULT_DEADLINE = 220.0


def _callme_outcome_message(to: str, data: dict) -> Optional[str]:
    """Turn a /result payload into a user-facing /callme outcome line, or None
    if the call is still in progress (timeout/missing) and we should stay quiet
    rather than send a misleading message."""
    if data.get("status") != "complete":
        return None
    result = data.get("result") or {}
    tcs = (result.get("twilio_call_status") or "").lower()
    answered_by = (result.get("answered_by") or "").lower()
    esc = html.escape(to)
    if tcs in {"busy", "no-answer", "canceled"}:
        return (
            f"📵 Couldn't reach you at <code>{esc}</code> — the line was busy or "
            "there was no answer. Run /callme to try again."
        )
    if tcs == "failed":
        return (
            f"⚠️ The call to <code>{esc}</code> failed — the number may be "
            "unreachable. Check it's correct and in E.164 (e.g. +14155551234)."
        )
    if answered_by.startswith("machine") or answered_by == "fax":
        return (
            f"📭 Reached voicemail at <code>{esc}</code>, not you. Run /callme "
            "again when you can pick up."
        )
    # Call connected and ran. Stay silent here: the bridge's _post_call_followups
    # already DMs the tenant ("☎️ Assistant call ended …") on a connected call.
    # This poller exists to cover the cases the bridge never reaches — busy /
    # no-answer / voicemail / failed — so it must not double-notify on success.
    return None


async def _report_callme_outcome(
    context: ContextTypes.DEFAULT_TYPE, chat_id: int, to: str, room: str
) -> None:
    """Poll the bridge for the call's outcome and DM the user only if it didn't
    connect (busy / no answer / voicemail / failed). Connected calls are left to
    the bridge's own post-call summary, so we never double-notify."""
    try:
        data = await wait_for_result(room, deadline_seconds=CALLME_RESULT_DEADLINE)
    except Exception as exc:  # noqa: BLE001
        log.warning("callme result poll failed for room=%s: %s", room, exc)
        return
    message = _callme_outcome_message(to, data)
    if not message:
        return
    try:
        await context.bot.send_message(chat_id=chat_id, text=message, parse_mode="HTML")
    except Exception as exc:  # noqa: BLE001
        log.warning("callme outcome notify failed for chat=%s: %s", chat_id, exc)


async def callme(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Call the user in assistant mode: the clone phones them and converses
    freely (and can summarize their email).

    With no argument, rings the number you saved by sharing your contact —
    "my agent, call me". Pass a number (/callme +14155551234) to ring a
    different phone just this once."""
    user = update.effective_user
    tenant = await _fetch_tenant(user.id) if user else None
    if not tenant:
        await update.message.reply_text("Run /register first.")
        return
    if not tenant.get("is_active", 1):
        await update.message.reply_text("Your account is inactive. Contact the operator.")
        return
    args = context.args or []
    digits = "".join(ch for ch in " ".join(args) if ch.isdigit())
    if digits:
        # Explicit override — ring a different phone this once.
        if len(digits) < 7:
            await update.message.reply_text(
                "That doesn't look like a phone number. Send /callme to ring "
                "your saved number, or /callme +14155551234 for a different one."
            )
            return
        to = "+" + digits
    else:
        # No argument — ring the number saved when you shared your contact.
        to = (tenant.get("phone") or "").strip()
        if not to:
            contact_kb = ReplyKeyboardMarkup(
                [[KeyboardButton("📱 Share my number", request_contact=True)]],
                resize_keyboard=True, one_time_keyboard=True,
            )
            await update.message.reply_text(
                "I don't have your number yet. Tap below to share it — then just "
                "send /callme and I'll ring you. (Or /callme +14155551234 to use "
                "a one-off number.)",
                reply_markup=contact_kb,
            )
            return
    await update.message.reply_text(f"Calling you at {to} in assistant mode…")
    out = await dial(
        to=to,
        reason="personal assistant call",
        language="en",
        tenant_id=tenant["id"],
        mode="assistant",
    )
    if out.startswith("Error"):
        await update.message.reply_text(out)
        return
    await update.message.reply_text(
        f"Call placed (room: <code>{html.escape(out)}</code>). "
        "Pick up and say e.g. “summarize my emails this week.”",
        parse_mode="HTML",
    )
    # Poll for the outcome in the background so a busy / no-answer / voicemail
    # result is reported instead of leaving the user staring at "pick up" for a
    # call that never connected. Background so other commands aren't blocked.
    task = asyncio.create_task(
        _report_callme_outcome(context, update.effective_chat.id, to, out)
    )
    _BG_TASKS.add(task)
    task.add_done_callback(_BG_TASKS.discard)


async def history(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    tenant = await _fetch_tenant(user.id) if user else None
    if not tenant:
        await update.message.reply_text("Run /register first.")
        return
    async with aiohttp.ClientSession() as sess:
        async with sess.get(
            f"{_bridge_url()}/history/{tenant['id']}",
            params={"limit": MAX_HISTORY_DISPLAYED},
            headers=_auth_headers(),
            timeout=aiohttp.ClientTimeout(total=10),
        ) as r:
            data = await r.json()
    rows = data.get("calls") or []
    if not rows:
        await update.message.reply_text("No calls yet. /call to place one.")
        return
    lines = [f"Last {len(rows)} call{'s' if len(rows) != 1 else ''}:\n"]
    for c in rows:
        started = c.get("started_at", "")[:16].replace("T", " ")
        status = c.get("status") or "pending"
        dest = c.get("destination", "")
        dur = c.get("duration_seconds") or 0.0
        answer = (c.get("answer") or "").replace("\n", " ")[:80]
        lines.append(f"• {started}  {dest}  [{status}, {dur:.0f}s]")
        if answer:
            lines.append(f"    {answer}")
    await update.message.reply_text("\n".join(lines))


async def voice_status(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    """`/voice` — show the tenant's current voice clone + how to replace it."""
    user = update.effective_user
    tenant = await _fetch_tenant(user.id) if user else None
    if not tenant:
        await update.message.reply_text("Run /register first.")
        return
    voice_id = tenant.get("voice_id")
    voice_name = tenant.get("voice_name") or "—"
    if voice_id:
        await update.message.reply_text(
            f"Your custom voice is active.\n"
            f"  uid: {voice_id}\n"
            f"  name: {voice_name}\n\n"
            "Send a fresh voice message or audio file (≥20s of clean speech) "
            "to replace it.\n"
            "Send /clear_voice to revert to the language default."
        )
    else:
        await update.message.reply_text(
            "No custom voice set — calls use the default per-language voice.\n\n"
            "To clone yours: record a voice message (or send an audio file) of "
            "≥20s of clean speech to this chat. I'll create the clone and "
            "use it on your future calls."
        )


async def clear_voice(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    """`/clear_voice` — drop the tenant's clone, revert to default."""
    user = update.effective_user
    tenant = await _fetch_tenant(user.id) if user else None
    if not tenant:
        await update.message.reply_text("Run /register first.")
        return
    old = tenant.get("voice_id")
    if not old:
        await update.message.reply_text("No custom voice to clear.")
        return
    await _tenants_db.set_tenant_voice(int(tenant["id"]), "", "")
    try:
        await _voices.delete_voice(old)
    except Exception:  # noqa: BLE001
        pass
    await update.message.reply_text("Cleared. Future calls use the language default.")


async def translate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """`/translate` — arm one-shot real-time translation. Pick a target language,
    then send a voice note: I'll speak it back translated, in your cloned voice."""
    user = update.effective_user
    tenant = await _fetch_tenant(user.id) if user else None
    if not tenant:
        await update.message.reply_text("Run /register first.")
        return
    langs = _translate.supported_languages()
    # Two per row keeps the keyboard compact.
    rows, row = [], []
    for i, lang in enumerate(langs, 1):
        row.append(InlineKeyboardButton(lang["name"], callback_data=f"xlate:{lang['code']}"))
        if i % 2 == 0:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    await update.message.reply_text(
        "🌍 Translate to which language?\n"
        "Pick one, then send a voice note — I'll speak it back translated"
        + (" in your cloned voice." if tenant.get("voice_id") else
           " (clone your voice first to hear it in your own voice)."),
        reply_markup=InlineKeyboardMarkup(rows),
    )


async def translate_pick_language(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Store the chosen target language and arm the next voice note for translation."""
    query = update.callback_query
    await query.answer()
    code = query.data.split(":", 1)[1]
    name = _translate.LANGUAGE_NAMES.get(code, code)
    context.user_data["translate_to"] = code
    await query.edit_message_text(
        f"🌍 Translating your next voice note to {name}. Send it now."
    )


async def translate_turn(update: Update, context: ContextTypes.DEFAULT_TYPE, tenant: dict) -> None:
    """Translate a single voice/audio message into the armed target language and
    reply with the translated audio in the tenant's cloned voice."""
    target = context.user_data.pop("translate_to", None)
    if not target:
        return
    msg = update.message
    file_obj = msg.voice or msg.audio
    if file_obj is None:
        return
    suffix = ".ogg"
    if msg.audio:
        mime = (msg.audio.mime_type or "").lower()
        if "mp3" in mime or "mpeg" in mime:
            suffix = ".mp3"
        elif "wav" in mime:
            suffix = ".wav"
        elif "m4a" in mime or "mp4" in mime:
            suffix = ".m4a"
    name = _translate.LANGUAGE_NAMES.get(target, target)
    await msg.chat.send_action("record_voice")
    try:
        tg_file = await file_obj.get_file()
        audio = bytes(await tg_file.download_as_bytearray())
        text, ogg_out = await _translate.translate_voice_note(
            audio, target, voice_id=tenant.get("voice_id") or None, suffix=suffix,
        )
    except Exception as e:  # noqa: BLE001
        log.exception("translate: failed")
        await msg.reply_text(f"Translation failed: {e}")
        return
    await msg.reply_voice(voice=ogg_out)
    if text:
        await msg.reply_text(f"🌍 {name}: {text}")


async def voice_chat_turn(update: Update, context: ContextTypes.DEFAULT_TYPE, tenant: dict) -> None:
    """One round of voice-note conversation with the clone: transcribe → reply
    in the cloned voice → grow memory. The phone-free way to talk to your clone."""
    msg = update.message
    await msg.chat.send_action("record_voice")
    try:
        tg_file = await msg.voice.get_file()
        ogg = bytes(await tg_file.download_as_bytearray())
        user_text = await _voice_chat.transcribe(ogg)
    except Exception as e:  # noqa: BLE001
        log.exception("voice chat: transcription failed")
        await msg.reply_text(f"Sorry, I couldn't hear that ({e}). Try again?")
        return
    if not user_text:
        await msg.reply_text("I didn't catch any speech in that — try again?")
        return

    history = context.user_data.setdefault("chat_history", [])
    try:
        answer = await _voice_chat.reply(tenant, history, user_text)
        ogg_out = await _voice_chat.synthesize(answer, tenant["voice_id"])
    except Exception as e:  # noqa: BLE001
        log.exception("voice chat: reply/synthesis failed")
        await msg.reply_text(f"Hit a snag generating my reply ({e}).")
        return

    await msg.reply_voice(voice=ogg_out)
    await msg.reply_text(f"🗣️ You: {user_text}\n🤖 {answer}")
    try:
        learned = await _voice_chat.learn_from_exchange(int(tenant["id"]), user_text, answer)
        if learned:
            await msg.reply_text(f"🧠 (remembered {learned} new thing{'s' if learned != 1 else ''})")
    except Exception as e:  # noqa: BLE001
        log.warning("voice chat: memory growth failed: %s", e)


async def handle_text_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Plain typed message → text reply from the clone. The text counterpart to
    voice_chat_turn: same LLM + memory + shared chat_history, but replies with
    text (voice notes still get voice replies). Registered after the /call
    ConversationHandler so it doesn't hijack that flow."""
    user = update.effective_user
    msg = update.message
    if not msg or not msg.text:
        return
    tenant = await _fetch_tenant(user.id) if user else None
    if not tenant:
        await msg.reply_text("Send /register first, then we can chat.")
        return
    await msg.chat.send_action("typing")
    history = context.user_data.setdefault("chat_history", [])
    try:
        answer = await _voice_chat.reply(tenant, history, msg.text, channel="text")
    except Exception as e:  # noqa: BLE001
        log.exception("text chat: reply failed")
        await msg.reply_text(f"Hit a snag generating my reply ({e}).")
        return
    await msg.reply_text(answer)
    try:
        learned = await _voice_chat.learn_from_exchange(int(tenant["id"]), msg.text, answer)
        if learned:
            await msg.reply_text(f"🧠 (remembered {learned} new thing{'s' if learned != 1 else ''})")
    except Exception as e:  # noqa: BLE001
        log.warning("text chat: memory growth failed: %s", e)


async def handle_audio_sample(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Voice notes/audio. With a clone already set, a voice note is a CHAT turn;
    otherwise it's a sample to clone (re-clone via /clear_voice first)."""
    user = update.effective_user
    tenant = await _fetch_tenant(user.id) if user else None
    if not tenant:
        await update.message.reply_text("Send /register first, then I'll clone your voice.")
        return

    # Armed by /translate → translate this clip instead of cloning/chatting.
    if context.user_data.get("translate_to") and (update.message.voice or update.message.audio):
        await translate_turn(update, context, tenant)
        return

    # Clone exists + this is a voice note → talk to the clone.
    if update.message.voice and tenant.get("voice_id"):
        await voice_chat_turn(update, context, tenant)
        return

    msg = update.message
    file_obj = None
    suffix = ".ogg"
    if msg.voice:
        file_obj = msg.voice
        suffix = ".ogg"
    elif msg.audio:
        file_obj = msg.audio
        # Telegram audio uploads keep the original mime — common: mp3, m4a, wav, ogg.
        mime = (msg.audio.mime_type or "").lower()
        if "mp3" in mime or "mpeg" in mime:
            suffix = ".mp3"
        elif "wav" in mime:
            suffix = ".wav"
        elif "m4a" in mime or "mp4" in mime:
            suffix = ".m4a"
        elif "ogg" in mime or "opus" in mime:
            suffix = ".ogg"

    if file_obj is None:
        return  # not an audio message — let other handlers pick it up

    # Consent gate: never clone a voice note silently. The sample could be
    # anyone's voice — require an explicit "it's my own voice" confirmation.
    context.user_data["pending_clone"] = {"file_id": file_obj.file_id, "suffix": suffix}
    keyboard = [[
        InlineKeyboardButton("✅ Yes, clone my voice", callback_data="clone_consent:yes"),
        InlineKeyboardButton("❌ Cancel", callback_data="clone_consent:no"),
    ]]
    await msg.reply_text(
        "Before I clone this: please confirm this recording is YOUR OWN voice "
        "and you consent to creating a synthetic clone of it for this agent.",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def clone_consent(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Runs the actual clone once the user confirms the sample is their own voice."""
    query = update.callback_query
    await query.answer()
    pending = context.user_data.pop("pending_clone", None)
    if query.data != "clone_consent:yes":
        await query.edit_message_text("Cancelled — your voice was not cloned.")
        return
    if not pending:
        await query.edit_message_text("That confirmation expired. Send the voice note again.")
        return
    user = update.effective_user
    tenant = await _fetch_tenant(user.id) if user else None
    if not tenant:
        await query.edit_message_text("Send /register first, then I'll clone your voice.")
        return

    msg = query.message
    suffix = pending["suffix"]
    await query.edit_message_text("Thanks — cloning your voice via Gradium…")
    try:
        tg_file = await context.bot.get_file(pending["file_id"])
        audio_bytes = await tg_file.download_as_bytearray()
    except Exception as e:  # noqa: BLE001
        await msg.reply_text(f"Couldn't fetch your audio from Telegram: {e}")
        return

    try:
        result = await _voices.clone_from_bytes(
            bytes(audio_bytes),
            name=f"gradphone:{tenant['name']}",
            suffix=suffix,
            description=f"Telegram clone for tenant_id={tenant['id']}",
        )
    except ValueError as e:
        await msg.reply_text(f"{e}")
        return
    except Exception as e:  # noqa: BLE001
        log.exception("voice clone failed")
        await msg.reply_text(f"Cloning failed: {e}")
        return

    uid = result.get("uid") or result.get("voice_id") or result.get("id")
    if not uid:
        await msg.reply_text(f"Gradium returned an unexpected response: {result}")
        return

    await _tenants_db.set_tenant_voice(
        int(tenant["id"]), uid, voice_name=f"gradphone:{tenant['name']}"
    )
    await msg.reply_text(
        f"Done. Your voice clone is active.\n"
        f"uid: <code>{html.escape(uid)}</code>\n\n"
        "🎙️ Send me a <b>voice note</b> anytime to talk to your clone — it replies "
        "in your voice and remembers what you tell it.\n"
        "/voice to inspect, /clear_voice to re-clone.",
        parse_mode="HTML",
    )


async def status(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    tenant = await _fetch_tenant(user.id) if user else None
    if not tenant:
        await update.message.reply_text("Run /register first.")
        return
    async with aiohttp.ClientSession() as sess:
        async with sess.get(
            f"{_bridge_url()}/calls/live",
            headers=_auth_headers(),
            timeout=aiohttp.ClientTimeout(total=10),
        ) as r:
            data = await r.json()
    mine = [c for c in (data.get("calls") or []) if c.get("tenant_id") == tenant["id"]]
    if not mine:
        await update.message.reply_text("No calls in flight.")
        return
    lines = [f"{len(mine)} call{'s' if len(mine) != 1 else ''} in flight:\n"]
    for c in mine:
        lines.append(
            f"• {c.get('destination', '?')}  phase={c.get('phase')}  "
            f"age={c.get('age_seconds')}s  room={c.get('room')}"
        )
    await update.message.reply_text("\n".join(lines))


def _allowed_telegram_ids() -> set[int]:
    """Parse ALLOWED_TELEGRAM_IDS (comma-separated Telegram user IDs)."""
    raw = os.environ.get("ALLOWED_TELEGRAM_IDS", "")
    ids: set[int] = set()
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if part.isdigit():
            ids.add(int(part))
    return ids


async def _gatekeeper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Group -1 guard that runs before every handler.

    Single-owner model (fails CLOSED for forks):
      - ALLOWED_TELEGRAM_IDS set → only the owner's Telegram ID may use the bot.
      - Else → the bot refuses everyone, unless ALLOW_INSECURE_LOCAL=1 is
        explicitly set for local dev. This stops a freshly-forked bot from
        being open to the entire internet by default.
    """
    allowed = _allowed_telegram_ids()
    user = update.effective_user
    uid = user.id if user else None

    if allowed:
        if uid in allowed:
            return
        denied = "Not authorized. This is a personal assistant for its owner only."
    elif os.environ.get("ALLOW_INSECURE_LOCAL", "").strip().lower() in ("1", "true", "yes"):
        return
    else:
        denied = "This bot isn't configured yet (set ALLOWED_TELEGRAM_IDS to the owner's Telegram ID)."

    msg = update.effective_message
    if msg is not None:
        try:
            await msg.reply_text(denied)
        except Exception:  # noqa: BLE001 - never let the refusal crash the gate
            pass
    raise ApplicationHandlerStop


def main() -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN env var is required")

    app = Application.builder().token(token).build()
    app.add_handler(TypeHandler(_Update, _gatekeeper), group=-1)

    conv = ConversationHandler(
        entry_points=[CommandHandler("call", call_start)],
        states={
            ASK_TO: [MessageHandler(filters.TEXT & ~filters.COMMAND, got_to)],
            ASK_TASK: [MessageHandler(filters.TEXT & ~filters.COMMAND, got_task)],
            ASK_LANG: [CallbackQueryHandler(got_language, pattern=r"^lang:")],
            CONFIRM: [CallbackQueryHandler(confirm, pattern=r"^confirm:")],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("whoami", whoami))
    app.add_handler(CommandHandler("register", register))
    app.add_handler(CommandHandler("history", history))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("web", web))
    app.add_handler(CommandHandler("callme", callme))
    app.add_handler(CommandHandler("voice", voice_status))
    app.add_handler(CommandHandler("clear_voice", clear_voice))
    app.add_handler(CommandHandler("translate", translate))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, handle_audio_sample))
    app.add_handler(MessageHandler(filters.CONTACT, save_contact))
    app.add_handler(CallbackQueryHandler(clone_consent, pattern=r"^clone_consent:"))
    app.add_handler(CallbackQueryHandler(translate_pick_language, pattern=r"^xlate:"))
    app.add_handler(conv)
    # Free-text chat — registered AFTER conv so the /call flow's text steps
    # take precedence while that conversation is active.
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_chat))

    log.info("gradphone bot starting against bridge %s", _bridge_url())
    app.run_polling()


if __name__ == "__main__":
    main()
