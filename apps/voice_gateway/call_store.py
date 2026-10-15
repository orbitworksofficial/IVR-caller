"""
Call persistence for the Voice Gateway.

Uses the service-role client (like the admin API) because Twilio webhooks
arrive before any tenant context exists. Isolation is therefore enforced in
code: tenant_id is always resolved from our own records (the dialled number,
then the call row) — never from anything the caller or Twilio supplies — and
every query below filters on it.

supabase-py is synchronous, so every call is pushed to a thread to keep the
event loop free for audio frames on other live calls.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from supabase import Client

logger = logging.getLogger(__name__)

# Planning prices from the implementation plan (Section 15) — rough per-call
# estimate only; the source of truth is each vendor's invoice.
_COST_TELEPHONY_PER_MIN = {"inbound": 0.0085, "outbound": 0.013}
_COST_STT_PER_MIN = 0.0043
_COST_TTS_PER_1K_CHARS = 0.10


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class CallStore:
    def __init__(self, supabase: Client):
        self.db = supabase

    async def _run(self, fn):
        return await asyncio.to_thread(fn)

    # ── Lookup ────────────────────────────────────────────────────────────────

    async def find_inbound_number(self, to_number: str) -> Optional[Dict[str, Any]]:
        def q():
            return (
                self.db.table("phone_numbers")
                .select("id, tenant_id, bot_id, phone_number, direction")
                .eq("phone_number", to_number)
                .eq("is_active", True)
                .in_("direction", ["inbound", "both"])
                .limit(1)
                .execute()
            )
        res = await self._run(q)
        return res.data[0] if res.data else None

    async def load_ivr_config(self, tenant_id: str, bot_id: str) -> Dict[str, Any]:
        def q():
            return (
                self.db.table("ivr_bot_configs")
                .select("*, voice_profiles(*)")
                .eq("tenant_id", tenant_id)
                .eq("bot_id", bot_id)
                .limit(1)
                .execute()
            )
        try:
            res = await self._run(q)
            return res.data[0] if res.data else {}
        except Exception as e:
            logger.warning(f"Could not load ivr_bot_configs for bot {bot_id}: {e}")
            return {}

    async def get_call(self, call_id: str) -> Optional[Dict[str, Any]]:
        def q():
            return self.db.table("calls").select("*").eq("id", call_id).limit(1).execute()
        res = await self._run(q)
        return res.data[0] if res.data else None

    # ── Writes ────────────────────────────────────────────────────────────────

    async def create_call(
        self,
        tenant_id: str,
        bot_id: str,
        phone_number_id: Optional[str],
        direction: str,
        from_number: str,
        to_number: str,
        call_sid: Optional[str],
        status: str = "in-progress",
    ) -> Dict[str, Any]:
        row = {
            "tenant_id": tenant_id,
            "bot_id": bot_id,
            "phone_number_id": phone_number_id,
            "direction": direction,
            "from_number": from_number,
            "to_number": to_number,
            "twilio_call_sid": call_sid,
            "status": status,
        }
        if status == "in-progress":
            row["answered_at"] = _now_iso()
        res = await self._run(lambda: self.db.table("calls").insert(row).execute())
        return res.data[0]

    async def mark_stream_started(self, call_id: str, tenant_id: str, stream_sid: str) -> None:
        await self._run(
            lambda: self.db.table("calls")
            .update({"twilio_stream_sid": stream_sid})
            .eq("id", call_id).eq("tenant_id", tenant_id).execute()
        )

    async def update_status_by_sid(self, call_sid: str, status: str, duration_s: Optional[int]) -> None:
        update: Dict[str, Any] = {"status": status}
        if duration_s is not None:
            update["duration_s"] = duration_s
            update["telephony_minutes"] = round(duration_s / 60.0, 3)
        if status in ("completed", "failed", "busy", "no-answer", "canceled"):
            update["ended_at"] = _now_iso()
        res = await self._run(
            lambda: self.db.table("calls").update(update).eq("twilio_call_sid", call_sid).execute()
        )

        # Outbound: an unanswered dial never opens a stream, so its attempt
        # outcome is only knowable here.
        unanswered = {"busy": "busy", "no-answer": "no_answer", "failed": "failed", "canceled": "failed"}
        if status in unanswered and res.data:
            await self._run(
                lambda: self.db.table("dial_attempts").update({"outcome": unanswered[status]})
                .eq("call_id", res.data[0]["id"]).is_("outcome", "null").execute()
            )

    async def save_recording(
        self, call_sid: str, recording_sid: str, recording_url: str, duration_s: Optional[int], status: str
    ) -> None:
        call = await self._run(
            lambda: self.db.table("calls").select("id, tenant_id")
            .eq("twilio_call_sid", call_sid).limit(1).execute()
        )
        if not call.data:
            logger.warning(f"Recording {recording_sid} for unknown call {call_sid}")
            return
        call_id, tenant_id = call.data[0]["id"], call.data[0]["tenant_id"]

        await self._run(
            lambda: self.db.table("call_recordings").upsert({
                "tenant_id": tenant_id,
                "call_id": call_id,
                "provider": "twilio",
                "recording_sid": recording_sid,
                "recording_url": recording_url,
                "duration_s": duration_s,
                "channels": 2,
                "status": status,
            }, on_conflict="recording_sid").execute()
        )
        await self._run(
            lambda: self.db.table("calls")
            .update({"recording_url": recording_url, "recording_sid": recording_sid})
            .eq("id", call_id).eq("tenant_id", tenant_id).execute()
        )

    # ── Outbound / compliance ─────────────────────────────────────────────────

    async def find_outbound_number(self, tenant_id: str, bot_id: str) -> Optional[Dict[str, Any]]:
        """The tenant's own caller ID for this bot. Never another tenant's number."""
        def q():
            return (
                self.db.table("phone_numbers")
                .select("id, tenant_id, bot_id, phone_number")
                .eq("tenant_id", tenant_id)
                .eq("bot_id", bot_id)
                .eq("is_active", True)
                .in_("direction", ["outbound", "both"])
                .limit(1)
                .execute()
            )
        res = await self._run(q)
        return res.data[0] if res.data else None

    async def get_campaign(self, tenant_id: str, campaign_id: str) -> Optional[Dict[str, Any]]:
        res = await self._run(
            lambda: self.db.table("call_campaigns").select("*")
            .eq("id", campaign_id).eq("tenant_id", tenant_id).limit(1).execute()
        )
        return res.data[0] if res.data else None

    async def is_on_dnc(self, tenant_id: str, phone_number: str) -> bool:
        res = await self._run(
            lambda: self.db.table("dnc_list").select("id")
            .eq("tenant_id", tenant_id).eq("phone_number", phone_number).limit(1).execute()
        )
        return bool(res.data)

    async def has_active_consent(self, tenant_id: str, phone_number: str, consent_type: str) -> bool:
        res = await self._run(
            lambda: self.db.table("consent_records").select("id")
            .eq("tenant_id", tenant_id).eq("phone_number", phone_number)
            .eq("consent_type", consent_type).is_("revoked_at", "null").limit(1).execute()
        )
        return bool(res.data)

    async def list_dial_attempts(
        self, tenant_id: str, phone_number: str, campaign_id: Optional[str]
    ) -> List[Dict[str, Any]]:
        """Real dials to this number (blocked attempts never reached the phone network)."""
        def q():
            query = (
                self.db.table("dial_attempts").select("id, dialed_at, outcome")
                .eq("tenant_id", tenant_id).eq("phone_number", phone_number)
                .not_.is_("dialed_at", "null")
            )
            if campaign_id:
                query = query.eq("campaign_id", campaign_id)
            return query.execute()
        res = await self._run(q)
        return res.data or []

    async def record_dial_attempt(self, row: Dict[str, Any]) -> Dict[str, Any]:
        res = await self._run(lambda: self.db.table("dial_attempts").insert(row).execute())
        return res.data[0] if res.data else row

    async def update_dial_attempt_for_call(self, call_id: str, update: Dict[str, Any]) -> None:
        await self._run(
            lambda: self.db.table("dial_attempts").update(update).eq("call_id", call_id).execute()
        )

    async def update_call(self, call_id: str, tenant_id: str, update: Dict[str, Any]) -> None:
        await self._run(
            lambda: self.db.table("calls").update(update)
            .eq("id", call_id).eq("tenant_id", tenant_id).execute()
        )

    async def get_call_reason(self, call_id: str, tenant_id: str) -> str:
        """Outbound calls: the campaign's call_reason, injected into the outbound prompt."""
        try:
            res = await self._run(
                lambda: self.db.table("dial_attempts").select("call_campaigns(call_reason)")
                .eq("call_id", call_id).eq("tenant_id", tenant_id).limit(1).execute()
            )
            if res.data and res.data[0].get("call_campaigns"):
                return res.data[0]["call_campaigns"].get("call_reason") or ""
        except Exception as e:
            logger.warning(f"Could not load call_reason for {call_id}: {e}")
        return ""

    async def opt_out(self, tenant_id: str, phone_number: str, call_id: Optional[str], evidence: str) -> None:
        """
        Caller asked not to be called again: DNC immediately and revoke call
        consent (plan §09 — internal opt-out honoured immediately).
        """
        await self._run(
            lambda: self.db.table("dnc_list").upsert({
                "tenant_id": tenant_id,
                "phone_number": phone_number,
                "source": "verbal_request",
                "reason": evidence[:500],
            }, on_conflict="tenant_id,phone_number").execute()
        )
        await self._run(
            lambda: self.db.table("consent_records").update({"revoked_at": _now_iso()})
            .eq("tenant_id", tenant_id).eq("phone_number", phone_number)
            .eq("consent_type", "call").is_("revoked_at", "null").execute()
        )

    async def finalize_call(
        self,
        call_id: str,
        tenant_id: str,
        direction: str,
        turns: List[Dict[str, Any]],
        summary: Dict[str, Any],
        stt_seconds: float,
        duration_s: int,
        model_used: Optional[str],
    ) -> None:
        """
        Writes the turn-by-turn transcript, the assembled transcript and the
        per-call cost telemetry. Runs from the stream's `finally`, so a dropped
        call still persists whatever completed before the drop.
        """
        tts_chars = sum(len(t["text"]) for t in turns if t["speaker"] == "agent")
        telephony_min = duration_s / 60.0
        est_cost = (
            telephony_min * _COST_TELEPHONY_PER_MIN.get(direction, 0.013)
            + (stt_seconds / 60.0) * _COST_STT_PER_MIN
            + (tts_chars / 1000.0) * _COST_TTS_PER_1K_CHARS
        )

        if turns:
            rows = [
                {
                    "call_id": call_id,
                    "tenant_id": tenant_id,
                    "speaker": t["speaker"],
                    "text": t["text"],
                    "turn_index": i,
                    "latency_ms": t.get("latency_ms"),
                    "model_used": model_used if t["speaker"] == "agent" else None,
                }
                for i, t in enumerate(turns)
            ]
            try:
                await self._run(lambda: self.db.table("call_turns").insert(rows).execute())
            except Exception as e:
                logger.error(f"Failed to save call_turns for {call_id}: {e}")

            full = "\n".join(f"{t['speaker'].capitalize()}: {t['text']}" for t in turns)
            try:
                await self._run(
                    lambda: self.db.table("call_transcripts").upsert({
                        "call_id": call_id,
                        "tenant_id": tenant_id,
                        "full_transcript": full,
                        "word_count": len(full.split()),
                    }, on_conflict="call_id").execute()
                )
            except Exception as e:
                logger.error(f"Failed to save call_transcripts for {call_id}: {e}")

        update = {
            "ended_at": _now_iso(),
            "turn_count": sum(1 for t in turns if t["speaker"] == "caller"),
            "call_summary": summary.get("summary") or None,
            "model_used": model_used,
            "stt_seconds": round(stt_seconds, 2),
            "tts_characters": tts_chars,
            "estimated_cost_usd": round(est_cost, 5),
        }
        # Only set duration if the status callback hasn't already written the
        # authoritative value from Twilio.
        try:
            existing = await self.get_call(call_id)
        except Exception:
            existing = None
        if duration_s and not (existing or {}).get("duration_s"):
            update["duration_s"] = duration_s
            update["telephony_minutes"] = round(telephony_min, 3)
        try:
            await self._run(
                lambda: self.db.table("calls").update(update)
                .eq("id", call_id).eq("tenant_id", tenant_id).execute()
            )
        except Exception as e:
            logger.error(f"Failed to finalize call {call_id}: {e}")
