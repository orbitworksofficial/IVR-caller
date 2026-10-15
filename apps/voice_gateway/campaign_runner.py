"""
Campaign runner — works through each active campaign's call list.

Every tick, per active campaign:
  1. Reconcile contacts in `dialing`: once their call has ended, mark them
     done / retry later / exhausted from the call's outcome.
  2. Dial due `pending` contacts, up to the campaign's max_concurrent live
     calls, via place_outbound_call() — so every dial still passes the
     fail-closed dial policy at dial time. The runner never bypasses it.
  3. Mark the campaign completed when nobody is left pending or dialing.

Claims are compare-and-set (pending → dialing only if still pending), so two
gateway instances can run the loop without dialling anyone twice.

Enabled with VOICE_CAMPAIGN_RUNNER=true; interval VOICE_CAMPAIGN_POLL_S (30).
"""

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Optional

from apps.voice_gateway.dial_policy import DEFAULT_MAX_ATTEMPTS, DEFAULT_RETRY_HOURS
from apps.voice_gateway.outbound import DialResult, place_outbound_call

logger = logging.getLogger(__name__)

TERMINAL_CALL_STATUSES = {"completed", "failed", "busy", "no-answer", "canceled"}
# Policy reasons that will not change by waiting — stop trying this contact.
PERMANENT_BLOCKS = {"dnc", "consent", "invalid_number"}

WINDOW_RECHECK = timedelta(minutes=15)
TRANSIENT_BACKOFF = timedelta(minutes=5)
STALE_CALL_AFTER = timedelta(hours=2)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _parse(value: Any) -> Optional[datetime]:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")) if value else None


# ── Pure state transitions ────────────────────────────────────────────────────

def after_dial(result: DialResult, contact: Dict[str, Any], retry_hours: float, now: datetime) -> Dict[str, Any]:
    """Contact update after a dial was attempted (placed or blocked)."""
    attempts = int(contact.get("attempts") or 0)

    if result.dialed:
        return {"status": "dialing", "current_call_id": result.call_id,
                "attempts": attempts + 1, "last_outcome": "dialing"}

    reason = result.reason
    if reason in PERMANENT_BLOCKS:
        return {"status": "blocked", "last_outcome": reason, "current_call_id": None}
    if reason == "attempts":
        return {"status": "exhausted", "last_outcome": reason, "current_call_id": None}
    if reason == "window":
        delay = WINDOW_RECHECK
    elif reason == "retry":
        delay = timedelta(hours=1)  # the policy keeps blocking until the interval has passed
    else:
        # twilio_error / check_error / no_outbound_number / campaign state: transient.
        delay = TRANSIENT_BACKOFF
    update = {"status": "pending", "last_outcome": reason, "next_attempt_at": _iso(now + delay),
              "current_call_id": None}
    if reason == "twilio_error":
        update["attempts"] = attempts + 1  # a dial_attempts row with dialed_at was written
    return update


def after_call(
    attempt_outcome: Optional[str],
    call_status: Optional[str],
    contact: Dict[str, Any],
    max_attempts: int,
    retry_hours: float,
    now: datetime,
) -> Optional[Dict[str, Any]]:
    """
    Contact update once its call has ended, or None while it is still live.
    A call stuck non-terminal past STALE_CALL_AFTER (lost status callback) is
    treated as failed so the contact can't block a concurrency slot forever.
    """
    claimed = _parse(contact.get("claimed_at"))
    stale = claimed is not None and now - claimed > STALE_CALL_AFTER
    if call_status not in TERMINAL_CALL_STATUSES and not stale:
        return None

    if attempt_outcome == "completed":
        return {"status": "done", "last_outcome": "answered", "current_call_id": None}

    outcome = attempt_outcome or ("stale" if stale and call_status not in TERMINAL_CALL_STATUSES else "failed")
    if int(contact.get("attempts") or 0) >= max_attempts:
        return {"status": "exhausted", "last_outcome": outcome, "current_call_id": None}
    return {"status": "pending", "last_outcome": outcome, "current_call_id": None,
            "next_attempt_at": _iso(now + timedelta(hours=retry_hours))}


# ── Loop ──────────────────────────────────────────────────────────────────────

class CampaignRunner:
    def __init__(
        self,
        store: Any,
        dial: Callable[..., Any] = place_outbound_call,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ):
        self.store = store
        self.dial = dial
        self.clock = clock

    async def tick(self) -> None:
        for campaign in await self.store.list_active_campaigns():
            try:
                await self._run_campaign(campaign)
            except Exception as e:
                logger.exception(f"Campaign {campaign.get('id')} tick failed: {e}")

    async def _run_campaign(self, campaign: Dict[str, Any]) -> None:
        cid, tenant_id, bot_id = str(campaign["id"]), str(campaign["tenant_id"]), campaign.get("bot_id")
        if not bot_id:
            logger.warning(f"Campaign {cid} has no bot_id; skipping")
            return

        config = await self.store.load_ivr_config(tenant_id, str(bot_id))
        max_attempts = int(campaign.get("max_attempts") or config.get("max_call_attempts") or DEFAULT_MAX_ATTEMPTS)
        retry_hours = float(config.get("retry_interval_hours") or DEFAULT_RETRY_HOURS)
        now = self.clock()

        # 1. Reconcile live calls.
        live = 0
        for contact in await self.store.list_contacts(cid, "dialing"):
            outcome, status = await self.store.get_call_progress(contact.get("current_call_id"))
            update = after_call(outcome, status, contact, max_attempts, retry_hours, now)
            if update is None:
                live += 1
            else:
                await self.store.update_contact(contact["id"], update)
                logger.info(f"Campaign {cid}: {contact['phone_number']} → {update['status']} ({update['last_outcome']})")

        # 2. Dial due contacts within the concurrency limit.
        slots = int(campaign.get("max_concurrent") or 2) - live
        if slots > 0:
            for contact in await self.store.due_contacts(cid, _iso(now), slots):
                if not await self.store.claim_contact(contact["id"], _iso(now)):
                    continue  # another runner took it
                result = await self.dial(
                    self.store, tenant_id, str(bot_id), contact["phone_number"], cid, contact.get("lead_id")
                )
                await self.store.update_contact(contact["id"], after_dial(result, contact, retry_hours, now))
                logger.info(f"Campaign {cid}: dial {contact['phone_number']} → {result.reason}")

        # 3. Complete the campaign once nobody is left to call.
        counts = await self.store.contact_status_counts(cid)
        if counts and not counts.get("pending") and not counts.get("dialing"):
            await self.store.set_campaign_status(cid, tenant_id, "completed")
            logger.info(f"Campaign {cid} completed: {counts}")

    async def run_forever(self, poll_s: float) -> None:
        logger.info(f"Campaign runner started (every {poll_s}s)")
        while True:
            try:
                await self.tick()
            except Exception as e:
                logger.exception(f"Campaign runner tick failed: {e}")
            await asyncio.sleep(poll_s)


def runner_enabled() -> bool:
    return os.getenv("VOICE_CAMPAIGN_RUNNER", "false").lower() == "true"


def poll_interval() -> float:
    try:
        return max(5.0, float(os.getenv("VOICE_CAMPAIGN_POLL_S", "30")))
    except ValueError:
        return 30.0
