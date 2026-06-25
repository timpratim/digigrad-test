"""Live web search for the assistant-mode voice agent, via Linkup.

The clone's LLM has a training cutoff, so when the caller asks about current
facts (today's news, weather, a recent event, a price) the agent calls the
web_search tool. This module wraps Linkup's synchronous /search endpoint in
"sourcedAnswer" mode: a natural-language query returns a short synthesized
answer plus its sources, which is exactly what we want to read back aloud.

Set in .env:
    LINKUP_API_KEY=your-api-key

search() returns a compact dict the LLM can speak, or raises
WebSearchNotConfigured / WebSearchError so the caller can tell the model the
web isn't available rather than crashing the call.

The Linkup SDK call is synchronous (blocking HTTP), so the bridge runs it via
asyncio.to_thread under a timeout — same pattern as get_email_summary.
"""

from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)

# "standard": single-iteration agentic search, ~1-3s, supports sourcedAnswer.
# "fast" is keyword-only (no synthesized answer); "deep" is 5-30s (too slow
# for a live phone turn). standard is the right default for voice.
_DEPTH = "standard"
# Keep the spoken answer tight and cap how many sources we hand back — every
# token returned flows back through the LLM and then TTS, so brevity = less
# dead air on the call.
_MAX_ANSWER_CHARS = 600
_MAX_SOURCES = 3


class WebSearchNotConfigured(RuntimeError):
    """LINKUP_API_KEY not set."""


class WebSearchError(RuntimeError):
    """Linkup request failed."""


def search(query: str) -> dict:
    """Run a Linkup sourced-answer search and return
    {"answer": str, "sources": [{"name", "url"}, …]}.

    Raises WebSearchNotConfigured if the API key is missing, WebSearchError on
    any request/parse failure.
    """
    api_key = os.environ.get("LINKUP_API_KEY", "").strip()
    if not api_key:
        raise WebSearchNotConfigured(
            "LINKUP_API_KEY not set — web search is not configured."
        )

    query = (query or "").strip()
    if not query:
        raise WebSearchError("Empty search query.")

    # Import lazily so a missing SDK doesn't break bridge import — only callers
    # that actually search pay for it, and the error stays local to this tool.
    try:
        from linkup import LinkupClient
    except ImportError as exc:  # pragma: no cover - depends on install
        raise WebSearchError("linkup-sdk is not installed.") from exc

    try:
        client = LinkupClient(api_key=api_key)
        resp = client.search(
            query=query,
            depth=_DEPTH,
            output_type="sourcedAnswer",
        )
    except Exception as exc:  # SDK raises various network/HTTP errors
        log.warning("linkup search failed: %s", exc)
        raise WebSearchError(f"Linkup search failed: {exc}") from exc

    # The SDK returns an object (or dict) with `answer` + `sources`; be
    # defensive about both shapes and missing fields.
    answer = _get(resp, "answer") or ""
    raw_sources = _get(resp, "sources") or []
    sources: list[dict] = []
    for src in list(raw_sources)[:_MAX_SOURCES]:
        name = _get(src, "name") or _get(src, "url") or ""
        url = _get(src, "url") or ""
        if name or url:
            sources.append({"name": str(name)[:120], "url": str(url)[:300]})

    answer = str(answer).strip()[:_MAX_ANSWER_CHARS]
    if not answer:
        raise WebSearchError("Linkup returned no answer.")
    return {"answer": answer, "sources": sources}


def _get(obj: object, key: str) -> object:
    """Read `key` from an object attribute or a dict, whichever it is."""
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)
