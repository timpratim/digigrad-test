"""Real-time speech translation via Gradium's speech-to-speech (s2s) engine.

Gradium's s2s endpoint (`wss://<host>/speech/s2s`) transcribes, translates, and
re-synthesizes speech in one pass: PCM audio in → translated `text` + translated
`audio` out, spoken in whatever `voice_id` you set. This is a *different* engine
from the gradbot LLM agent used on phone calls — there's no LLM in the loop, it's
a literal interpreter.

The killer demo for a voice-clone product: feed it a tenant's CLONED voice id and
it speaks the translation back in their own voice. "Hear yourself speak a language
you don't know."

This module is a thin, finite-clip client over that streaming endpoint, used by
the Telegram `/translate` flow:

    Telegram OGG/Opus
        → ffmpeg → PCM16 mono 24 kHz
        → Gradium s2s (target_language + voice_id)
        → translated text + PCM16 mono 48 kHz
        → WAV → ffmpeg → OGG/Opus → Telegram voice note

It mirrors the streaming protocol in Demo-Apps/Translation/main.py, but instead of
relaying a live mic forever it pushes one finite clip and drains the response until
the engine goes quiet.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import os
import struct
import subprocess

log = logging.getLogger(__name__)

# The s2s engine captures at 24 kHz and returns synthesis at 48 kHz. These must
# match what we feed/expect — they are the same constants the demo surfaces via
# /api/config so the two sides never disagree.
IN_SAMPLE_RATE = 24_000
OUT_SAMPLE_RATE = 48_000

DEFAULT_URL = "https://api.gradium.ai/api"

# Default voice id per target language (matches DEFAULT_VOICE_IDS in the demo).
# These are s2s voices; a tenant's own clone uid overrides them when available.
DEFAULT_VOICE_IDS = {
    "en": "_6Aslh2DxfmnRLmP",
    "fr": "25AzBFyp6svYnJsj",
    "es": "sVLgzKMqaptUdaY8",
    "de": "SqFfhmAgR2XdN83R",
    "pt": "AByHrwi1S-yLzW-s",
}

LANGUAGE_NAMES = {
    "en": "English",
    "fr": "French",
    "es": "Spanish",
    "de": "German",
    "pt": "Portuguese",
}

# How many ms of audio per outbound frame, and how long to keep draining the
# response after the last input frame before we decide the engine is done.
# The engine sends a terminal `end_of_stream` after our EOS, so the idle
# timeout is only a fallback for a stalled connection — keep it generous, well
# above the natural inter-message gaps in the engine's output stream (~1-3s).
_FRAME_MS = 80
_IDLE_TIMEOUT_S = float(os.environ.get("TRANSLATE_IDLE_TIMEOUT_S", "8.0"))
_OVERALL_TIMEOUT_S = float(os.environ.get("TRANSLATE_OVERALL_TIMEOUT_S", "60.0"))
# Silence padded before the clip (lets the STT engine warm up so it doesn't clip
# the first words) and after it (flushes the final turn through the engine).
_LEAD_SILENCE_MS = 300
_FLUSH_SILENCE_MS = 1200


def supported_languages() -> list[dict[str, str]]:
    """[{code, name}] for the languages with a built-in default voice."""
    return [{"code": c, "name": LANGUAGE_NAMES.get(c, c)} for c in DEFAULT_VOICE_IDS]


def default_voice_id(target_language: str) -> str | None:
    return os.environ.get("GRADIUM_TRANSLATE_VOICE_ID") or DEFAULT_VOICE_IDS.get(
        target_language
    )


def _s2s_url() -> str:
    """Build the wss s2s endpoint from the configured Gradium s2s base URL.

    Per the current Gradium docs the s2s endpoint lives on the same host as the
    REST API (``wss://api.gradium.ai/api/speech/s2s``), so we default to
    GRADIUM_URL → GRADIUM_BASE_URL → the documented host, in that order.
    """
    base_url = (
        os.environ.get("GRADIUM_URL")
        or os.environ.get("GRADIUM_BASE_URL")
        or DEFAULT_URL
    ).rstrip("/")
    if base_url.startswith("http://"):
        base_url = "ws://" + base_url[len("http://") :]
    elif base_url.startswith("https://"):
        base_url = "wss://" + base_url[len("https://") :]
    elif not base_url.startswith(("ws://", "wss://")):
        local = "localhost" in base_url or "127.0.0.1" in base_url
        base_url = ("ws://" if local else "wss://") + base_url
    return f"{base_url}/speech/s2s"


def _ffmpeg(args: list[str], stdin: bytes) -> bytes:
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", *args],
        input=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {proc.stderr.decode('utf-8', 'replace')[:300]}")
    return proc.stdout


def _ogg_to_pcm16(audio: bytes, suffix: str = ".ogg") -> bytes:
    """Any Telegram audio (OGG/Opus, mp3, m4a, wav) → PCM16 mono @ 24 kHz."""
    return _ffmpeg(
        ["-i", "pipe:0", "-f", "s16le", "-acodec", "pcm_s16le",
         "-ac", "1", "-ar", str(IN_SAMPLE_RATE), "pipe:1"],
        audio,
    )


def _pcm16_to_wav(pcm: bytes, sample_rate: int) -> bytes:
    """Wrap raw little-endian PCM16 mono in a minimal WAV container."""
    buf = io.BytesIO()
    data_len = len(pcm)
    byte_rate = sample_rate * 2  # mono, 16-bit
    buf.write(b"RIFF")
    buf.write(struct.pack("<I", 36 + data_len))
    buf.write(b"WAVE")
    buf.write(b"fmt ")
    buf.write(struct.pack("<IHHIIHH", 16, 1, 1, sample_rate, byte_rate, 2, 16))
    buf.write(b"data")
    buf.write(struct.pack("<I", data_len))
    buf.write(pcm)
    return buf.getvalue()


def _wav_to_ogg_opus(wav: bytes) -> bytes:
    return _ffmpeg(
        ["-i", "pipe:0", "-c:a", "libopus", "-b:a", "32k", "-f", "ogg", "pipe:1"],
        wav,
    )


async def _connect(url: str, api_key: str):
    """Open the s2s websocket, tolerating the websockets header-kwarg rename
    (additional_headers in >=14, extra_headers before)."""
    import websockets

    headers = [("x-api-key", api_key)]
    try:
        return await websockets.connect(url, additional_headers=headers, open_timeout=20)
    except TypeError:
        return await websockets.connect(url, extra_headers=headers, open_timeout=20)


def _decode_audio_field(msg: dict) -> bytes:
    """Pull base64 PCM out of an s2s 'audio' message, whatever the field name."""
    for key in ("audio", "data", "pcm", "payload"):
        val = msg.get(key)
        if isinstance(val, str) and val:
            try:
                return base64.b64decode(val)
            except Exception:  # noqa: BLE001
                return b""
    return b""


async def translate_voice_note(
    audio_bytes: bytes,
    target_language: str,
    *,
    voice_id: str | None = None,
    suffix: str = ".ogg",
) -> tuple[str, bytes]:
    """Translate one voice clip into ``target_language``.

    Returns ``(translated_text, ogg_opus_bytes)`` — the text shown in the chat
    and the audio played back. ``voice_id`` should be the tenant's clone uid when
    available so the translation is spoken in their own voice; otherwise a
    per-language default is used.

    Raises on configuration/transport errors; the caller surfaces them.
    """
    target_language = (target_language or "en").lower()
    api_key = os.environ.get("GRADIUM_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("GRADIUM_API_KEY is required for translation")
    voice = voice_id or default_voice_id(target_language)
    if not voice:
        raise RuntimeError(
            f"No voice id for target language '{target_language}'. "
            "Clone a voice first or set GRADIUM_TRANSLATE_VOICE_ID."
        )

    pcm_in = await asyncio.to_thread(_ogg_to_pcm16, audio_bytes, suffix)
    if not pcm_in:
        raise RuntimeError("Could not decode any audio from that clip.")

    setup_message = json.dumps({
        "type": "setup",
        "model_name": os.environ.get("GRADIUM_TRANSLATE_MODEL") or "s2s-translate",
        "stt_model_name": "stt-translate",
        "tts_model_name": "default",
        "input_format": "pcm",
        "output_format": "pcm",
        # json_config is a nested object on the wire, NOT a JSON-encoded string.
        "json_config": {"target_language": target_language},
        "voice_id": voice,
    })

    url = _s2s_url()
    upstream = await _connect(url, api_key)

    text_parts: list[str] = []
    audio_chunks: list[bytes] = []

    frame_bytes = int(IN_SAMPLE_RATE * (_FRAME_MS / 1000.0)) * 2  # mono 16-bit

    def _silence(ms: int) -> bytes:
        return b"\x00" * (int(IN_SAMPLE_RATE * (ms / 1000.0)) * 2)

    async def _send_audio() -> None:
        await upstream.send(setup_message)
        # Lead-in silence warms up the STT engine so it doesn't clip the opening
        # words; the clip itself; then trailing silence to flush the final turn.
        # We push frames as fast as the socket accepts them (yielding to the
        # receiver between each) rather than pacing at real time: this is a
        # finite clip, the engine buffers it fine, and real-time pacing stretched
        # the gaps between output messages to ~3s — right at the idle timeout,
        # which intermittently aborted the drain with no audio.
        stream = _silence(_LEAD_SILENCE_MS) + pcm_in + _silence(_FLUSH_SILENCE_MS)
        for off in range(0, len(stream), frame_bytes):
            chunk = stream[off:off + frame_bytes]
            await upstream.send(json.dumps({
                "type": "audio",
                "audio": base64.b64encode(chunk).decode("ascii"),
            }))
            await asyncio.sleep(0)  # yield to the receiver without throttling
        # Signal end-of-input so the engine flushes the final turn and emits its
        # terminal `end_of_stream`; without this it never knows the clip is done.
        await upstream.send(json.dumps({"type": "end_of_stream"}))

    async def _recv_loop() -> None:
        # Drain responses until the engine goes quiet for _IDLE_TIMEOUT_S.
        while True:
            try:
                raw = await asyncio.wait_for(upstream.recv(), timeout=_IDLE_TIMEOUT_S)
            except asyncio.TimeoutError:
                return
            except Exception:  # noqa: BLE001 - connection closed by peer
                return
            try:
                msg = json.loads(raw)
            except (ValueError, TypeError):
                continue
            kind = msg.get("type")
            if kind == "text":
                piece = (msg.get("text") or "").strip()
                if piece:
                    text_parts.append(piece)
            elif kind == "audio":
                pcm = _decode_audio_field(msg)
                if pcm:
                    audio_chunks.append(pcm)
            elif kind == "end_of_stream":
                # Terminal message after our EOS — the engine is done; stop
                # draining immediately instead of waiting out the idle timeout.
                return
            elif kind == "error":
                raise RuntimeError(msg.get("message") or "translation engine error")

    try:
        send_task = asyncio.create_task(_send_audio())
        recv_task = asyncio.create_task(_recv_loop())
        # The receiver returns once the engine idles; that's our natural end.
        # Bound the whole thing so a stuck stream can't hang the bot.
        _, pending = await asyncio.wait(
            {recv_task}, timeout=_OVERALL_TIMEOUT_S
        )
        for t in pending:
            t.cancel()
        if not send_task.done():
            send_task.cancel()
        await asyncio.gather(send_task, recv_task, return_exceptions=True)
    finally:
        try:
            await upstream.close()
        except Exception:  # noqa: BLE001
            pass

    pcm_out = b"".join(audio_chunks)
    if not pcm_out:
        raise RuntimeError(
            "The translation engine returned no audio — try a longer, clearer clip."
        )
    wav = _pcm16_to_wav(pcm_out, OUT_SAMPLE_RATE)
    ogg = await asyncio.to_thread(_wav_to_ogg_opus, wav)
    return " ".join(text_parts).strip(), ogg
