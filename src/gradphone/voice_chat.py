"""Voice-note chat with your clone, over Telegram — no phone call needed.

Pipeline per voice note:
    Telegram OGG/Opus
        → ffmpeg → PCM16 mono 16k → Gradium STT → user text
        → LLM chat (system prompt carries the tenant's memory digest)
        → reply text
        → Gradium TTS (the tenant's cloned voice) → WAV
        → ffmpeg → OGG/Opus → Telegram voice note

Memory: the digest is injected so the clone "knows" the caller; after each
exchange we run the post-call extractor so it keeps learning. This reuses the
same memory + LLM plumbing as the live phone path — it's the phone-free way to
exercise and demo the clone.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess

import aiohttp

from . import memory as memory_mod
from . import websearch
from .business_agent import language_name

log = logging.getLogger(__name__)

_STT_SAMPLE_RATE = 24000  # Gradium STT operates at 24 kHz
_MAX_HISTORY_TURNS = 12  # rolling user+assistant messages kept for context
_LLM_TIMEOUT = 30
_MAX_TOOL_ROUNDS = 3  # cap web_search → LLM round-trips per reply
_WEB_SEARCH_TIMEOUT = 10.0  # seconds for one Linkup call


def _ffmpeg(args: list[str], stdin: bytes) -> bytes:
    """Run ffmpeg with bytes in/out via temp files (small clips; robust)."""
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", *args],
        input=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {proc.stderr.decode('utf-8', 'replace')[:300]}")
    return proc.stdout


def _ogg_to_pcm16(ogg: bytes) -> bytes:
    """Telegram voice (OGG/Opus) → raw little-endian PCM16 mono @ 24 kHz."""
    return _ffmpeg(
        ["-i", "pipe:0", "-f", "s16le", "-acodec", "pcm_s16le",
         "-ac", "1", "-ar", str(_STT_SAMPLE_RATE), "pipe:1"],
        ogg,
    )


def _wav_to_ogg_opus(wav: bytes) -> bytes:
    """Gradium TTS WAV → OGG/Opus for Telegram's reply_voice."""
    return _ffmpeg(
        ["-i", "pipe:0", "-c:a", "libopus", "-b:a", "32k", "-f", "ogg", "pipe:1"],
        wav,
    )


def _gradium_client():
    import gradium

    api_key = os.environ.get("GRADIUM_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("GRADIUM_API_KEY is required for voice chat")
    base_url = os.environ.get("GRADIUM_BASE_URL", "").strip() or None
    if base_url:
        return gradium.GradiumClient(api_key=api_key, base_url=base_url)
    return gradium.GradiumClient(api_key=api_key)


async def transcribe(ogg_bytes: bytes) -> str:
    """Voice note → text via Gradium STT (24 kHz int16 samples)."""
    import gradium
    import numpy as np

    pcm = await asyncio.to_thread(_ogg_to_pcm16, ogg_bytes)
    samples = np.frombuffer(pcm, dtype=np.int16)
    client = _gradium_client()
    setup = gradium.STTSetup(model_name="default", input_format="pcm")
    result = await client.stt(setup, samples, sample_rate=_STT_SAMPLE_RATE)
    return (getattr(result, "text", "") or "").strip()


async def synthesize(text: str, voice_id: str) -> bytes:
    """Reply text → OGG/Opus voice note in the tenant's cloned voice."""
    import gradium

    client = _gradium_client()
    setup = gradium.TTSSetup(model_name="default", voice_id=voice_id, output_format="wav")
    result = await client.tts(setup, text)
    wav = getattr(result, "raw_data", None)
    if not wav:
        raise RuntimeError("Gradium TTS returned no audio")
    return await asyncio.to_thread(_wav_to_ogg_opus, wav)


def _system_prompt(
    name: str,
    memory_digest: str,
    language: str = "en",
    channel: str = "voice",
    web_search_enabled: bool = False,
) -> str:
    lang = language_name(language)
    block = (
        "\nWHAT YOU ALREADY KNOW ABOUT THEM (from past chats — use it naturally, "
        "don't recite):\n" + memory_digest + "\n"
        if memory_digest.strip() else ""
    )
    if channel == "text":
        medium = "casual text chat"
        style = (
            "You're texting, so reply in clear written text. You MAY use short lists "
            "and include specifics like names and addresses when they're what's asked for."
        )
        if web_search_enabled:
            style += (
                " You can look things up on the live web with the web_search tool. "
                "Use it whenever they ask about current facts that may be outside your "
                "training knowledge — today's news, weather, recent events, prices, "
                "scores, anything time-sensitive — instead of guessing. After searching, "
                "answer in your own words and add the source links on their own lines."
            )
    else:
        medium = "casual voice chat"
        style = (
            "Be warm, concise, and natural — one or two sentences per reply, like a "
            "quick voice message. This will be read aloud by text-to-speech, so never "
            "use markdown, bullet points, emoji, or stage directions."
        )
    return (
        f"You are {name}'s personal assistant, speaking as their voice clone in a "
        f"{medium}. {style}\n"
        f"{block}"
        "\nIMPORTANT — answer the request directly in THIS reply. You have no way to "
        "send anything separately, 'put together' a list later, or follow up in another "
        "message; there is no background task. If they ask for a list or details, give "
        "it now. If you don't actually have the information (e.g. live or current data), "
        "say so plainly — never promise to send or compile something you can't deliver "
        "right here.\n"
        f"\nReply in {lang} unless they switch languages."
    )


def web_search_available() -> bool:
    """True when Linkup is configured, so the text assistant can search the web."""
    return bool(os.environ.get("LINKUP_API_KEY", "").strip())


def _web_search_tool_schema() -> dict:
    """OpenAI-style function schema for the web_search tool (text chat)."""
    return {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "Search the live web for current facts that may be outside your "
                "training knowledge — today's news, weather, recent events, prices, "
                "sports scores, anything time-sensitive or freshly changed. Returns a "
                "short sourced answer. Use it whenever the user asks something you're "
                "not confident is current; do NOT guess at recent facts."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "Natural-language question. Be specific; include the "
                            "entity, date, or place when known."
                        ),
                    },
                },
                "required": ["query"],
            },
        },
    }


async def _run_web_search(query: str) -> dict:
    """Execute one web_search tool call; never raises — returns a dict the LLM
    can read, with an `error` key when the search couldn't run."""
    query = (query or "").strip()
    if not query:
        return {"error": "Empty search query — ask what to look up."}
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(websearch.search, query), timeout=_WEB_SEARCH_TIMEOUT
        )
    except asyncio.TimeoutError:
        log.warning("text chat: web_search timed out for %r", query[:120])
        return {"error": "The search took too long — say you couldn't pull it up."}
    except websearch.WebSearchNotConfigured:
        return {"error": "Web search isn't set up."}
    except websearch.WebSearchError as exc:
        log.warning("text chat: web_search failed: %s", exc)
        return {"error": "The search failed — say you couldn't look it up right now."}


async def _chat(messages: list[dict], tools: list[dict] | None = None,
                max_tokens: int = 300) -> dict:
    """One LLM completion against the configured OpenAI-compatible endpoint.

    Returns the raw assistant message dict (so callers can inspect tool_calls);
    pass ``tools`` to expose function calling.
    """
    base = os.environ.get("LLM_BASE_URL", "").strip().rstrip("/")
    model = os.environ.get("LLM_MODEL", "").strip()
    if not base or not model:
        raise RuntimeError("LLM_BASE_URL / LLM_MODEL not set")
    api_key = (os.environ.get("OPENAI_API_KEY", "").strip()
               or os.environ.get("GRADIUM_API_KEY", "").strip())
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    payload: dict = {
        "model": model, "messages": messages,
        "temperature": 0.6, "max_tokens": max_tokens,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    async with aiohttp.ClientSession() as sess:
        async with sess.post(
            f"{base}/chat/completions", json=payload, headers=headers,
            timeout=aiohttp.ClientTimeout(total=_LLM_TIMEOUT),
        ) as r:
            r.raise_for_status()
            data = await r.json()
    return data["choices"][0]["message"]


async def _complete_with_tools(messages: list[dict], tools: list[dict] | None,
                               max_tokens: int) -> str:
    """Run the LLM, servicing any web_search tool calls, until it returns prose.

    ``messages`` is mutated in place with the assistant + tool turns. Without
    tools this is a single completion.
    """
    msg: dict = {}
    for _ in range(_MAX_TOOL_ROUNDS if tools else 1):
        msg = await _chat(messages, tools=tools, max_tokens=max_tokens)
        tool_calls = msg.get("tool_calls") or []
        if not tool_calls:
            break
        messages.append(msg)  # assistant turn that requested the tools
        for call in tool_calls:
            fn = call.get("function") or {}
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except (ValueError, TypeError):
                args = {}
            if fn.get("name") == "web_search":
                result = await _run_web_search(args.get("query", ""))
            else:
                result = {"error": f"Unknown tool {fn.get('name')!r}."}
            messages.append({
                "role": "tool",
                "tool_call_id": call.get("id"),
                "content": json.dumps(result),
            })
    else:
        # Exhausted the round budget while still asking for tools — force a
        # final prose answer with what we have.
        msg = await _chat(messages, max_tokens=max_tokens)
    return (msg.get("content") or "").strip()


async def reply(
    tenant: dict,
    history: list[dict],
    user_text: str,
    channel: str = "voice",
) -> str:
    """Produce the clone's reply to user_text, given rolling history.

    Reads the tenant's memory digest into the system prompt; mutates ``history``
    in place (appends the user + assistant turns, trimmed). Fire-and-forget
    memory growth happens in the caller after the reply is sent.
    """
    tenant_id = int(tenant["id"])
    digest = await memory_mod.render_digest(tenant_id)
    # Web search is text-only: spoken replies are synthesized by TTS, where
    # source links and longer answers don't belong.
    use_web = channel == "text" and web_search_available()
    system = _system_prompt(
        tenant.get("name") or "the user", digest,
        channel=channel, web_search_enabled=use_web,
    )
    messages = [{"role": "system", "content": system}, *history,
                {"role": "user", "content": user_text}]
    tools = [_web_search_tool_schema()] if use_web else None
    answer = await _complete_with_tools(
        messages, tools, max_tokens=500 if channel == "text" else 300,
    )
    history.append({"role": "user", "content": user_text})
    history.append({"role": "assistant", "content": answer})
    del history[:-_MAX_HISTORY_TURNS]  # keep the tail only
    return answer


async def learn_from_exchange(tenant_id: int, user_text: str, reply_text: str) -> int:
    """Grow memory from one exchange (best-effort)."""
    return await memory_mod.extract_and_store(
        tenant_id, [("caller", user_text), ("agent", reply_text)], room="telegram-voice"
    )
