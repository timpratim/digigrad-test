"""Tenants + call history, backed by the dual SQLite/Postgres layer in db.py.

Two principals interact with the bridge:

- **Operator** — the person running the bridge. Authenticates with
  ``BRIDGE_API_KEY``. No quota, no rate limit. Calls placed without a
  ``tenant_id`` are operator calls.
- **Tenant** — a Telegram-registered user. Has a row in ``tenants``,
  is rate-limited and quota-enforced, and every call is persisted to
  the ``calls`` table for history lookup.

Every tenant is isolated by ``tenant_id`` — this is the multi-tenant model
(one profile per workshop participant: their voice, memory, and call history).

Persistence is SQLite by default (``GRADPHONE_DB``) or Postgres when
``DATABASE_TYPE=postgresql`` / ``DATABASE_URL`` is set — see db.py. All access
is async via SQLAlchemy. The SQL here is portable across both dialects.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from . import db


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def init_db() -> None:
    """Create the tables if they don't exist (SQLite/dev) or rely on Alembic
    (Postgres). Idempotent — safe to call on every startup."""
    await db.init_db()


async def set_tenant_voice(tenant_id: int, voice_id: str, voice_name: str = "") -> None:
    """Persist a Gradium voice UID + display name for the tenant."""
    await db.execute(
        "UPDATE tenants SET voice_id = :vid, voice_name = :vn WHERE id = :id",
        vid=voice_id or None, vn=voice_name or None, id=tenant_id,
    )


async def register_tenant(telegram_id: int, name: str) -> int:
    """Register a Telegram user as a tenant. Returns the internal tenant_id.

    Idempotent — if the telegram_id already exists, returns the existing
    tenant_id (name is NOT updated). ``ON CONFLICT DO NOTHING`` is supported by
    both SQLite (>=3.24) and Postgres.
    """
    await db.execute(
        "INSERT INTO tenants (telegram_id, name, created_at) "
        "VALUES (:tid, :name, :now) ON CONFLICT (telegram_id) DO NOTHING",
        tid=telegram_id, name=name, now=_now(),
    )
    row = await db.fetch_one(
        "SELECT id FROM tenants WHERE telegram_id = :tid", tid=telegram_id,
    )
    return int(row["id"]) if row else 0


_TENANT_COLS = ("id, telegram_id, name, created_at, is_active, "
                "custom_calls_per_day, voice_id, voice_name, phone")


def _norm_phone(raw: str) -> str:
    """Normalize to strict E.164 (leading '+' + digits) to match Twilio's From."""
    digits = "".join(ch for ch in (raw or "") if ch.isdigit())
    return "+" + digits if digits else ""


async def set_tenant_phone(tenant_id: int, phone: str) -> None:
    await db.execute(
        "UPDATE tenants SET phone = :phone WHERE id = :id",
        phone=_norm_phone(phone) or None, id=tenant_id,
    )


async def get_tenant_by_phone(phone: str) -> Optional[dict]:
    """Look up a tenant by registered phone (E.164). Routes inbound calls by
    caller ID."""
    normalized = _norm_phone(phone)
    if not normalized:
        return None
    return await db.fetch_one(
        f"SELECT {_TENANT_COLS} FROM tenants WHERE phone = :phone", phone=normalized,
    )


async def get_tenant_by_telegram(telegram_id: int) -> Optional[dict]:
    return await db.fetch_one(
        f"SELECT {_TENANT_COLS} FROM tenants WHERE telegram_id = :tid", tid=telegram_id,
    )


async def get_tenant_by_id(tenant_id: int) -> Optional[dict]:
    return await db.fetch_one(
        f"SELECT {_TENANT_COLS} FROM tenants WHERE id = :id", id=tenant_id,
    )


async def list_tenants() -> list[dict]:
    return await db.fetch_all(
        f"SELECT {_TENANT_COLS} FROM tenants ORDER BY created_at ASC"
    )


async def record_call_start(
    room: str,
    tenant_id: Optional[int],
    twilio_call_sid: str,
    destination: str,
    task: str,
    language: str,
    business_name: str = "",
) -> None:
    await db.execute(
        "INSERT INTO calls "
        "(room, tenant_id, twilio_call_sid, destination, task, language, business_name, started_at, status) "
        "VALUES (:room, :tenant_id, :sid, :dest, :task, :lang, :bn, :now, 'pending') "
        "ON CONFLICT (room) DO UPDATE SET "
        "  tenant_id = excluded.tenant_id, twilio_call_sid = excluded.twilio_call_sid, "
        "  destination = excluded.destination, task = excluded.task, "
        "  language = excluded.language, business_name = excluded.business_name, "
        "  started_at = excluded.started_at, status = excluded.status",
        room=room, tenant_id=tenant_id, sid=twilio_call_sid, dest=destination,
        task=task, lang=language, bn=business_name, now=_now(),
    )


async def record_call_end(
    room: str,
    status: str,
    answer: str = "",
    confidence: str = "",
    twilio_call_status: str = "",
    answered_by: str = "",
    duration_seconds: float = 0.0,
) -> None:
    """Mark a call as ended. Only writes if the row is still ``pending`` —
    keeps the first-finisher's outcome (the WS-side business_result usually
    wins over the Twilio-side terminal status, because it has more info)."""
    await db.execute(
        "UPDATE calls SET ended_at = :now, duration_seconds = :dur, status = :status, "
        "answer = :answer, confidence = :conf, twilio_call_status = :tcs, answered_by = :ab "
        "WHERE room = :room AND status = 'pending'",
        now=_now(), dur=duration_seconds, status=status, answer=answer,
        conf=confidence, tcs=twilio_call_status, ab=answered_by, room=room,
    )


async def get_call(room: str) -> Optional[dict]:
    return await db.fetch_one("SELECT * FROM calls WHERE room = :room", room=room)


async def count_calls_today(tenant_id: int) -> int:
    today = datetime.now(timezone.utc).date().isoformat()
    row = await db.fetch_one(
        "SELECT COUNT(*) AS n FROM calls WHERE tenant_id = :tid AND started_at >= :today",
        tid=tenant_id, today=today,
    )
    return int(row["n"]) if row else 0


async def list_calls(tenant_id: int, limit: int = 10) -> list[dict]:
    return await db.fetch_all(
        "SELECT room, destination, task, language, business_name, started_at, ended_at, "
        "status, answer, confidence, duration_seconds, answered_by, twilio_call_status "
        "FROM calls WHERE tenant_id = :tid ORDER BY started_at DESC LIMIT :lim",
        tid=tenant_id, lim=limit,
    )
