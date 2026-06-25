"""CLI dispatcher: POST /dial to the running bridge, then poll /result.

Usage:
    python -m gradphone.dial +33144581010 "Ask if they have a table for 2 at 8pm tonight" \
        --language fr --business "La Cagouille"

The bridge must be running (see README): the bridge process is the one that
holds the gradbot session state, so dispatch goes through its HTTP endpoint
rather than calling dispatch_gradbot_call() in this process.

After dispatch the CLI polls GET /result/<room> every 5 seconds with a
default deadline of 10 minutes, then prints the outcome. Use --no-wait
to dispatch only.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

import aiohttp


def _auth_headers() -> dict:
    headers = {}
    bridge_key = os.environ.get("BRIDGE_API_KEY", "").strip()
    if bridge_key:
        headers["Authorization"] = f"Bearer {bridge_key}"
    return headers


async def dial(
    to: str,
    reason: str,
    language: str = "en",
    business_name: str = "",
    allow_booking: bool = False,
    bridge_url: str | None = None,
    tenant_id: int | None = None,
    mode: str = "business",
) -> str:
    bridge_url = (bridge_url or os.environ.get("GRADBOT_BRIDGE_URL", "http://127.0.0.1:8082")).rstrip("/")
    payload = {
        "to": to,
        "reason": reason,
        "language": language,
        "mode": mode,
        "business_name": business_name,
        "allow_booking": allow_booking,
    }
    if tenant_id is not None:
        payload["tenant_id"] = tenant_id
    async with aiohttp.ClientSession() as sess:
        async with sess.post(
            f"{bridge_url}/dial",
            json=payload,
            headers=_auth_headers(),
            timeout=aiohttp.ClientTimeout(total=15),
        ) as r:
            data = await r.json()
            if r.status != 200 or "error" in data:
                return f"Error: {data.get('error', f'bridge http={r.status}')}"
            return data.get("room", "Error: bridge returned no room")


async def wait_for_result(
    room: str,
    bridge_url: str | None = None,
    poll_interval: float = 5.0,
    deadline_seconds: float = 600.0,
) -> dict:
    """Poll /result/<room> until the call completes or the deadline expires.

    Returns the parsed result dict on success, or a dict like
    ``{"status": "timeout", "room": room}`` if the deadline is hit.
    """
    bridge_url = (bridge_url or os.environ.get("GRADBOT_BRIDGE_URL", "http://127.0.0.1:8082")).rstrip("/")
    deadline = asyncio.get_event_loop().time() + deadline_seconds
    async with aiohttp.ClientSession() as sess:
        while True:
            try:
                async with sess.get(
                    f"{bridge_url}/result/{room}",
                    headers=_auth_headers(),
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as r:
                    data = await r.json()
            except aiohttp.ClientError as e:
                data = {"status": "error", "error": str(e), "room": room}
            status = data.get("status")
            if status == "complete":
                return data
            if status in {"missing", "error"}:
                return data
            if asyncio.get_event_loop().time() >= deadline:
                return {"status": "timeout", "room": room}
            await asyncio.sleep(poll_interval)


def _format_result(data: dict) -> str:
    if data.get("status") != "complete":
        return json.dumps(data, indent=2)
    result = data.get("result", {})
    br = result.get("business_result") or {}
    lines = [
        f"Room:       {data.get('room')}",
        f"Framework:  {result.get('framework')}",
        f"Duration:   {result.get('duration_seconds', 0):.1f}s",
        f"Answered:   {result.get('answered_by') or 'human'}",
        f"Status:     {br.get('status', 'unknown')}",
        f"Confidence: {br.get('confidence', '-')}",
        f"Answer:     {br.get('answer', '')}",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Dispatch a gradphone outbound call and wait for the result.")
    parser.add_argument("to", help="Destination phone in E.164 format, e.g. +33144581010")
    parser.add_argument("reason", help="Task for the agent to complete on the call")
    parser.add_argument("--language", default="en", choices=["en", "fr", "pt"])
    parser.add_argument("--business", default="", help="Business name (optional, for prompt context)")
    parser.add_argument("--allow-booking", action="store_true", help="Permit the agent to make bookings")
    parser.add_argument("--bridge-url", default=None, help="Override GRADBOT_BRIDGE_URL")
    parser.add_argument("--no-wait", action="store_true", help="Dispatch only, don't poll for result")
    parser.add_argument("--deadline", type=float, default=600.0, help="Seconds to wait for the result (default 600)")
    args = parser.parse_args()

    out = asyncio.run(
        dial(
            to=args.to,
            reason=args.reason,
            language=args.language,
            business_name=args.business,
            allow_booking=args.allow_booking,
            bridge_url=args.bridge_url,
        )
    )
    if out.startswith("Error"):
        print(out)
        sys.exit(1)
    room = out
    print(f"Dispatched: room={room}")
    if args.no_wait:
        return
    print("Waiting for result… (Ctrl-C to stop polling; the call continues)")
    data = asyncio.run(wait_for_result(room, bridge_url=args.bridge_url, deadline_seconds=args.deadline))
    print()
    print(_format_result(data))


if __name__ == "__main__":
    main()
