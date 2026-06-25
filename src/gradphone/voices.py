"""Voice-clone uploads via Gradium.

Thin wrapper around ``gradium.GradiumClient.voice_create``. Audio bytes
come in from Telegram (voice notes / audio uploads) or the web UI
(multipart file upload). We write them to a tempfile (Gradium SDK wants
a path), call the API, get back a voice UID, and let the caller persist
that UID on the tenant row.

A tenant with ``voice_id`` set gets that voice on every outbound call
regardless of language (Gradium's voice clones are language-agnostic;
the language hint in SessionConfig still controls the LLM's behaviour).
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

# Gradium docs recommend 30s+ for a clean clone. We'll accept anything,
# but warn for very short samples.
MIN_RECOMMENDED_SECONDS = 20


def _gradium_client():
    """Build a GradiumClient from GRADIUM_API_KEY (+ optional base URL)."""
    import gradium  # lazy — keeps tests light

    api_key = os.environ.get("GRADIUM_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("GRADIUM_API_KEY env var is required for voice cloning")
    base_url = os.environ.get("GRADIUM_BASE_URL", "").strip() or None
    if base_url:
        return gradium.GradiumClient(api_key=api_key, base_url=base_url)
    return gradium.GradiumClient(api_key=api_key)


async def clone_from_bytes(
    audio_bytes: bytes,
    *,
    name: str,
    suffix: str = ".ogg",
    description: Optional[str] = None,
    start_s: float = 0.0,
) -> dict:
    """Upload raw audio bytes as a new Gradium voice clone.

    Returns the Gradium voice metadata dict (most importantly, ``uid``).
    Raises on failure — caller surfaces the error to the user.

    ``suffix`` should match the content type (".ogg" for Telegram voice
    notes, ".mp3" / ".wav" / ".m4a" for user uploads). Gradium infers
    ``input_format`` from the extension.
    """
    if not audio_bytes:
        raise ValueError("empty audio payload")
    if len(audio_bytes) < 4096:
        # Anything under ~4KB is almost certainly too short to clone usefully.
        raise ValueError(
            f"audio sample is too short ({len(audio_bytes)} bytes); send at least "
            f"{MIN_RECOMMENDED_SECONDS}s of clean speech"
        )

    client = _gradium_client()
    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    try:
        tmp.write(audio_bytes)
        tmp.close()
        log.info("voice_create: %d bytes -> %s (name=%r)", len(audio_bytes), tmp.name, name)
        voice = await client.voice_create(
            audio_file=Path(tmp.name),
            name=name,
            description=description,
            start_s=start_s,
        )
        log.info("voice_create OK: uid=%s name=%s", voice.get("uid"), voice.get("name"))
        return voice
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
        # GradiumClient may hold a session open — best-effort close.
        try:
            close = getattr(client, "close", None)
            if close is not None:
                await close()
        except Exception:  # noqa: BLE001
            pass


async def delete_voice(voice_id: str) -> bool:
    """Remove a previously-cloned voice from Gradium. Idempotent on 404."""
    if not voice_id:
        return False
    client = _gradium_client()
    try:
        await client.voice_delete(voice_id)
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("voice_delete failed for %s: %s", voice_id, e)
        return False
    finally:
        try:
            close = getattr(client, "close", None)
            if close is not None:
                await close()
        except Exception:  # noqa: BLE001
            pass
