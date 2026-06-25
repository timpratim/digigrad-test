"""Per-tenant rate limiting — daily quota + concurrent in-flight cap.

Operator calls (no ``tenant_id``) bypass both limits.

Daily quota is counted from the SQLite ``calls`` table (persistent across
restarts). Concurrent in-flight is counted in process memory — when the
bridge restarts, in-flight is reset to 0.
"""

from __future__ import annotations

import os
from collections import defaultdict
from typing import Optional

from .tenants import count_calls_today, get_tenant_by_id

DEFAULT_DAILY_QUOTA = int(os.environ.get("DEFAULT_DAILY_QUOTA", "20"))
DEFAULT_CONCURRENT_CAP = int(os.environ.get("DEFAULT_CONCURRENT_CAP", "3"))

_in_flight: dict[int, int] = defaultdict(int)


async def reserve(tenant_id: Optional[int]) -> tuple[bool, str]:
    """Check both limits and reserve an in-flight slot if allowed.

    Returns ``(ok, reason)``. If ``ok`` is True the caller MUST eventually
    call :func:`release` exactly once with the same ``tenant_id``.
    """
    if tenant_id is None:
        return True, ""

    tenant = await get_tenant_by_id(tenant_id)
    if not tenant:
        return False, f"Unknown tenant_id {tenant_id}."
    if not tenant.get("is_active", 1):
        return False, "Tenant account is disabled."

    quota = tenant.get("custom_calls_per_day") or DEFAULT_DAILY_QUOTA
    if _in_flight[tenant_id] >= DEFAULT_CONCURRENT_CAP:
        return False, (
            f"Concurrent call limit reached ({DEFAULT_CONCURRENT_CAP}). "
            "Wait for an in-flight call to finish."
        )

    today_count = await count_calls_today(tenant_id)
    if today_count >= quota:
        return False, (
            f"Daily quota reached ({today_count}/{quota}). Resets at midnight UTC."
        )

    _in_flight[tenant_id] += 1
    return True, ""


def release(tenant_id: Optional[int]) -> None:
    if tenant_id is None:
        return
    if _in_flight[tenant_id] > 0:
        _in_flight[tenant_id] -= 1


def in_flight_count(tenant_id: int) -> int:
    return _in_flight[tenant_id]
