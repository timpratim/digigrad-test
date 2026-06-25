"""Batch outbound caller.

Fires many ``dispatch_gradbot_call`` invocations with a concurrency cap,
tracks per-target progress in memory, and exposes the campaign state for
polling. Campaign-level results live in ``_CAMPAIGNS`` until the bridge
restarts — they're an *in-flight* index, not durable history. Per-call
durability comes from the ``calls`` table in :mod:`gradphone.tenants`.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

log = logging.getLogger(__name__)


@dataclass
class CampaignTarget:
    to: str
    reason: str
    language: str = "en"
    business_name: str = ""
    allow_booking: bool = False
    label: str = ""


@dataclass
class CampaignResult:
    target: CampaignTarget
    room: str = ""
    status: str = "pending"  # pending / dialed / failed
    error: str = ""


@dataclass
class CampaignState:
    id: str
    tenant_id: Optional[int]
    results: list[CampaignResult]
    concurrency: int
    started_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None


_CAMPAIGNS: dict[str, CampaignState] = {}

DispatchFn = Callable[..., Awaitable[str]]


async def start_campaign(
    targets: list[CampaignTarget],
    dispatch_fn: DispatchFn,
    tenant_id: Optional[int] = None,
    concurrency: int = 3,
) -> str:
    """Schedule the campaign as a background task and return its id.

    ``dispatch_fn`` is called per target as:
        await dispatch_fn(to=..., spec=BusinessCallSpec, tenant_id=...)
    and is expected to return the room name or an "Error: ..." string.
    """
    campaign_id = uuid.uuid4().hex[:12]
    results = [CampaignResult(target=t) for t in targets]
    state = CampaignState(
        id=campaign_id,
        tenant_id=tenant_id,
        results=results,
        concurrency=max(1, concurrency),
    )
    _CAMPAIGNS[campaign_id] = state
    asyncio.create_task(_drive(state, dispatch_fn))
    log.info("campaign %s started — %d targets, concurrency=%d",
             campaign_id, len(targets), concurrency)
    return campaign_id


async def _drive(state: CampaignState, dispatch_fn: DispatchFn) -> None:
    from .business_agent import BusinessCallSpec

    sem = asyncio.Semaphore(state.concurrency)

    async def _one(result: CampaignResult) -> None:
        async with sem:
            target = result.target
            spec = BusinessCallSpec(
                task=target.reason,
                language=(target.language or "en").lower(),
                business_name=target.business_name,
                destination=target.to,
                allow_booking=target.allow_booking,
            )
            try:
                room = await dispatch_fn(to=target.to, spec=spec, tenant_id=state.tenant_id)
            except Exception as e:  # noqa: BLE001
                result.status = "failed"
                result.error = str(e)
                return
            if isinstance(room, str) and room.startswith("Error"):
                result.status = "failed"
                result.error = room
            else:
                result.room = room
                result.status = "dialed"

    await asyncio.gather(*[_one(r) for r in state.results])
    state.completed_at = time.time()
    log.info("campaign %s drive complete — dispatched %d targets",
             state.id, len(state.results))


def get(campaign_id: str) -> Optional[CampaignState]:
    return _CAMPAIGNS.get(campaign_id)


def snapshot(state: CampaignState) -> dict:
    return {
        "id": state.id,
        "tenant_id": state.tenant_id,
        "started_at": state.started_at,
        "completed_at": state.completed_at,
        "concurrency": state.concurrency,
        "total": len(state.results),
        "dialed": sum(1 for r in state.results if r.status == "dialed"),
        "failed": sum(1 for r in state.results if r.status == "failed"),
        "pending": sum(1 for r in state.results if r.status == "pending"),
        "targets": [
            {
                "to": r.target.to,
                "label": r.target.label,
                "language": r.target.language,
                "reason": r.target.reason,
                "status": r.status,
                "room": r.room,
                "error": r.error,
            }
            for r in state.results
        ],
    }
