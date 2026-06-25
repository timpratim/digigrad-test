"""Exercise the agent memory loop end-to-end without a phone call.

Uses a throwaway SQLite DB so it never touches your real data. Runs the same
functions the live call path uses: explicit `remember`, post-call extraction
(hits the configured LLM endpoint), the digest that gets injected into the
prompt, and `recall`.

    python scripts/memory_demo.py
"""

import asyncio
import os
import tempfile

from dotenv import load_dotenv

load_dotenv()
# Isolate: point the DB at a temp file BEFORE importing modules that bind it.
_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
os.environ["GRADPHONE_DB"] = _tmp.name

from gradphone import memory, tenants  # noqa: E402

SAMPLE_TRANSCRIPT = [
    ("agent", "Hey, it's your assistant. What's up?"),
    ("caller", "Can you remind me — I always want my calls scheduled in the morning, never after 2pm."),
    ("agent", "Got it, mornings only."),
    ("caller", "Also my dentist is Dr. Lemoine on Rue Saint-Honoré, I need a cleaning next month."),
    ("agent", "Noted. Anything else?"),
    ("caller", "That's it, thanks."),
]


async def main() -> None:
    await tenants.init_db()
    tid = await tenants.register_tenant(telegram_id=424242, name="demo")
    print(f"tenant_id={tid}  db={_tmp.name}\n")

    print("1) explicit `remember` (what the tool does mid-call):")
    await memory.add_memory(tid, "Wife's name is Sara", source="remember_tool")
    print("   stored: Wife's name is Sara\n")

    print("2) post-call extraction against the LLM endpoint:")
    base = os.environ.get("LLM_BASE_URL", "")
    print(f"   LLM_BASE_URL={base or '(unset)'}  LLM_MODEL={os.environ.get('LLM_MODEL','(unset)')}")
    n = await memory.extract_and_store(tid, SAMPLE_TRANSCRIPT, room="demo-room")
    print(f"   extracted + stored {n} fact(s)"
          + ("" if n else "  ← extraction returned nothing (endpoint unreachable or no facts)") + "\n")

    print("3) digest injected into the next call's prompt:")
    digest = await memory.render_digest(tid)
    print("\n".join("   " + ln for ln in digest.splitlines()) or "   (empty)")
    print()

    print("4) `recall` tool — search by topic:")
    for q in ("dentist", "morning", "Sara"):
        hits = await memory.search_memories(tid, q)
        print(f"   recall({q!r}) -> {hits}")


if __name__ == "__main__":
    asyncio.run(main())
