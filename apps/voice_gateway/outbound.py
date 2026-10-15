"""
Outbound dialing service.

    trigger → dial policy (consent + DNC + window + attempts) ──blocked──► dial_attempts(*_blocked), no dial
                    │ allowed
                    ▼
            calls row + dial_attempts row → Twilio create call (with AMD)
                    │
            /voice/outbound-answer?call_id=…  ── machine ──► approved voicemail, hang up
                    │ human
                    ▼
            same Media Stream / VoiceOrchestrator pipeline as inbound

The model never dials: this is only reachable from the authenticated trigger
endpoint or server-side code (plan §07 — "the backend decides").
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from apps.voice_gateway import twilio_utils
from apps.voice_gateway.dial_policy import check_dial

logger = logging.getLogger(__name__)


@dataclass
class DialResult:
    dialed: bool
    reason: str
    call_id: Optional[str] = None
    call_sid: Optional[str] = None
    detail: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


async def place_outbound_call(
    store: Any,
    tenant_id: str,
    bot_id: str,
    to_number: str,
    campaign_id: Optional[str] = None,
    lead_id: Optional[str] = None,
) -> DialResult:
    campaign = None
    if campaign_id:
        campaign = await store.get_campaign(tenant_id, campaign_id)
        if not campaign:
            return DialResult(False, "campaign_not_found")
        if campaign.get("status") != "active":
            return DialResult(False, "campaign_not_active", detail=f"status={campaign.get('status')}")

    caller_id = await store.find_outbound_number(tenant_id, bot_id)
    if not caller_id:
        return DialResult(False, "no_outbound_number", detail="no active outbound phone_numbers row for this bot")

    attempt_base = {
        "tenant_id": tenant_id,
        "campaign_id": campaign_id,
        "lead_id": lead_id,
        "phone_number": to_number,
    }

    # Immediately before the dial — not at list-import time (plan §06).
    decision = await check_dial(store, tenant_id, bot_id, to_number, campaign)
    if not decision.allowed:
        logger.info(f"Outbound dial to {to_number} blocked for tenant {tenant_id}: {decision.reason} ({decision.detail})")
        await store.record_dial_attempt({
            **attempt_base,
            "outcome": decision.attempt_outcome,
            "blocking_reason": decision.detail or decision.reason,
        })
        return DialResult(False, decision.reason, detail=decision.detail)

    previous = await store.list_dial_attempts(tenant_id, to_number, campaign_id)
    call = await store.create_call(
        tenant_id=tenant_id,
        bot_id=bot_id,
        phone_number_id=str(caller_id["id"]),
        direction="outbound",
        from_number=caller_id["phone_number"],
        to_number=to_number,
        call_sid=None,
        status="initiated",
    )
    call_id = str(call["id"])
    await store.record_dial_attempt({
        **attempt_base,
        "call_id": call_id,
        "attempt_number": len(previous) + 1,
        "dialed_at": datetime.now(timezone.utc).isoformat(),
    })

    base = twilio_utils.public_base_url()
    try:
        call_sid = await twilio_utils.create_outbound_call(
            to_number=to_number,
            from_number=caller_id["phone_number"],
            answer_url=f"{base}/voice/outbound-answer?call_id={call_id}",
            status_url=f"{base}/voice/status",
        )
    except Exception as e:
        logger.error(f"Outbound dial failed for call {call_id}: {e}")
        await store.update_call(call_id, tenant_id, {"status": "failed", "ended_at": datetime.now(timezone.utc).isoformat()})
        await store.update_dial_attempt_for_call(call_id, {"outcome": "failed", "blocking_reason": str(e)[:500]})
        return DialResult(False, "twilio_error", call_id=call_id, detail=str(e))

    await store.update_call(call_id, tenant_id, {"twilio_call_sid": call_sid})
    logger.info(f"Outbound call {call_id} ({call_sid}) placed to {to_number} for tenant {tenant_id}")
    return DialResult(True, "ok", call_id=call_id, call_sid=call_sid)
