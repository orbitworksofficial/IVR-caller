"""
Voice Gateway — Twilio Programmable Voice + Media Streams.

Runs as its own process (separate from the chat API) because media handling is
latency-sensitive; it shares the same database and AI layer.

    POST /voice/inbound           Twilio "A call comes in" webhook → TwiML <Connect><Stream>
    WS   /voice/stream            Bidirectional Media Stream for the live call
    POST /voice/status            Twilio call status callback
    POST /voice/recording-status  Twilio recording status callback
    POST /voice/outbound/dial     Internal trigger: place a compliance-checked outbound call
    POST /voice/outbound-answer   Twilio answer webhook for outbound calls (AMD branch)
    GET  /voice/health

Run locally:
    uvicorn apps.voice_gateway.main:app --port 8001
"""

import asyncio
import hmac
import logging
import os
from typing import Dict, Optional

from fastapi import FastAPI, Header, HTTPException, Request, WebSocket
from fastapi.responses import Response
from pydantic import BaseModel

from apps.voice_gateway import twilio_utils
from apps.voice_gateway.call_store import CallStore, _now_iso
from apps.voice_gateway.components import build_call_components
from apps.voice_gateway.campaign_runner import CampaignRunner, poll_interval, runner_enabled
from apps.voice_gateway.outbound import place_outbound_call
from apps.voice_gateway.session import CallSession

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title="OrbitWorks Voice Gateway", version="0.1.0")

# Overridable in tests.
app.state.store = None
app.state.components_factory = build_call_components

NOT_IN_SERVICE = "Sorry, this number is not currently in service. Goodbye."
UNAVAILABLE = "Sorry, we can't take your call right now. Please try again shortly. Goodbye."


REQUIRED_ENV = (
    "TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "VOICE_PUBLIC_BASE_URL",
    "DEEPGRAM_API_KEY", "ELEVENLABS_API_KEY",
)


@app.on_event("startup")
async def _check_env() -> None:
    missing = [k for k in REQUIRED_ENV if not os.getenv(k)]
    if missing:
        # Without TWILIO_AUTH_TOKEN every webhook fails signature checks (403),
        # so a misconfigured gateway rejects calls instead of half-working.
        logger.warning(f"Voice Gateway missing env: {', '.join(missing)} — calls will fail until set.")


@app.on_event("startup")
async def _start_campaign_runner() -> None:
    if runner_enabled():
        app.state.campaign_task = asyncio.create_task(
            CampaignRunner(get_store()).run_forever(poll_interval())
        )


@app.on_event("shutdown")
async def _stop_campaign_runner() -> None:
    task = getattr(app.state, "campaign_task", None)
    if task:
        task.cancel()


def get_store() -> CallStore:
    if app.state.store is None:
        from apps.api.supabase_client import get_supabase_admin
        app.state.store = CallStore(get_supabase_admin())
    return app.state.store


def _twiml(body: str) -> Response:
    return Response(content=body, media_type="application/xml")


async def _verified_form(request: Request) -> Dict[str, str]:
    """Parses the webhook body and rejects it unless Twilio signed it."""
    form = {k: str(v) for k, v in (await request.form()).items()}
    if not twilio_utils.signature_validation_enabled():
        return form

    url = f"{twilio_utils.public_base_url()}{request.url.path}"
    if request.url.query:
        url += f"?{request.url.query}"
    if not twilio_utils.is_valid_signature(
        twilio_utils.auth_token(), url, form, request.headers.get("X-Twilio-Signature")
    ):
        logger.warning(f"Rejected unsigned/invalid Twilio request to {request.url.path}")
        raise HTTPException(status_code=403, detail="Invalid Twilio signature")
    return form


@app.get("/voice/health")
async def health():
    return {"status": "ok", "service": "voice-gateway"}


@app.post("/voice/inbound")
async def inbound_call(request: Request):
    form = await _verified_form(request)
    call_sid, from_number, to_number = form.get("CallSid", ""), form.get("From", ""), form.get("To", "")
    store = get_store()

    number = await store.find_inbound_number(to_number)
    if not number or not number.get("bot_id"):
        logger.warning(f"Inbound call {call_sid} to unconfigured number {to_number}")
        return _twiml(twilio_utils.twiml_say_hangup(NOT_IN_SERVICE))

    try:
        call = await store.create_call(
            tenant_id=str(number["tenant_id"]),
            bot_id=str(number["bot_id"]),
            phone_number_id=str(number["id"]),
            direction="inbound",
            from_number=from_number,
            to_number=to_number,
            call_sid=call_sid,
        )
    except Exception as e:
        logger.error(f"Could not create call row for {call_sid}: {e}")
        return _twiml(twilio_utils.twiml_say_hangup(UNAVAILABLE))

    call_id = str(call["id"])
    logger.info(f"Inbound call {call_sid} from {from_number} → tenant {number['tenant_id']} (call {call_id})")
    return _stream_twiml(call_id)


def _stream_twiml(call_id: str) -> Response:
    ws_base = twilio_utils.public_base_url().replace("https://", "wss://").replace("http://", "ws://")
    return _twiml(twilio_utils.twiml_connect_stream(
        f"{ws_base}/voice/stream",
        {"call_id": call_id, "token": twilio_utils.stream_token(call_id)},
    ))


@app.websocket("/voice/stream")
async def media_stream(ws: WebSocket):
    await ws.accept()
    await CallSession(ws, get_store(), app.state.components_factory).run()


@app.post("/voice/status")
async def call_status(request: Request):
    form = await _verified_form(request)
    duration = form.get("CallDuration")
    await get_store().update_status_by_sid(
        form.get("CallSid", ""),
        form.get("CallStatus", ""),
        int(duration) if duration and duration.isdigit() else None,
    )
    return Response(status_code=204)


@app.post("/voice/recording-status")
async def recording_status(request: Request):
    form = await _verified_form(request)
    duration = form.get("RecordingDuration")
    await get_store().save_recording(
        call_sid=form.get("CallSid", ""),
        recording_sid=form.get("RecordingSid", ""),
        recording_url=form.get("RecordingUrl", ""),
        duration_s=int(duration) if duration and duration.isdigit() else None,
        status=form.get("RecordingStatus", "completed"),
    )
    return Response(status_code=204)


# ── Outbound ──────────────────────────────────────────────────────────────────

class DialRequest(BaseModel):
    tenant_id: str
    bot_id: str
    to_number: str
    campaign_id: Optional[str] = None
    lead_id: Optional[str] = None


@app.post("/voice/outbound/dial")
async def outbound_dial(body: DialRequest, x_voice_api_key: Optional[str] = Header(default=None)):
    """
    Internal trigger for an outbound call (CRM follow-up, no-show, campaign
    runner). Server-to-server only: requires VOICE_DIAL_API_KEY, and is
    disabled entirely when that key is not configured.
    """
    expected = os.getenv("VOICE_DIAL_API_KEY", "")
    if not expected:
        raise HTTPException(status_code=503, detail="Outbound dialing is not enabled")
    if not x_voice_api_key or not hmac.compare_digest(x_voice_api_key, expected):
        raise HTTPException(status_code=401, detail="Invalid API key")

    result = await place_outbound_call(
        get_store(), body.tenant_id, body.bot_id, body.to_number, body.campaign_id, body.lead_id
    )
    return {
        "dialed": result.dialed,
        "reason": result.reason,
        "detail": result.detail,
        "call_id": result.call_id,
        "call_sid": result.call_sid,
    }


VOICEMAIL_OUTCOMES = {"machine_end_beep", "machine_end_silence", "machine_end_other", "machine_start"}


@app.post("/voice/outbound-answer")
async def outbound_answer(request: Request, call_id: str):
    """
    Twilio requests this once the callee answers and AMD has decided who it is.
    Human (or AMD unsure) → same media-stream pipeline as inbound.
    Machine → the tenant's approved voicemail message only, never the AI agent.
    """
    form = await _verified_form(request)
    store = get_store()
    call = await store.get_call(call_id)
    if not call or call.get("twilio_call_sid") not in (None, form.get("CallSid")):
        logger.warning(f"Outbound answer for unknown/mismatched call {call_id}")
        return _twiml(twilio_utils.twiml_voicemail(None))

    tenant_id = str(call["tenant_id"])
    answered_by = form.get("AnsweredBy", "unknown")
    await store.update_call(call_id, tenant_id, {"answered_at": _now_iso()})

    if answered_by in VOICEMAIL_OUTCOMES or answered_by == "fax":
        config = await store.load_ivr_config(tenant_id, str(call["bot_id"]))
        message = config.get("voicemail_message") if answered_by != "fax" else None
        await store.update_call(call_id, tenant_id, {"outcome": "voicemail", "status": "completed"})
        await store.update_dial_attempt_for_call(call_id, {"outcome": "voicemail"})
        logger.info(f"Outbound call {call_id} reached {answered_by}; voicemail={'yes' if message else 'no'}")
        return _twiml(twilio_utils.twiml_voicemail(message))

    await store.update_call(call_id, tenant_id, {"status": "in-progress"})
    await store.update_dial_attempt_for_call(call_id, {"outcome": "completed"})
    logger.info(f"Outbound call {call_id} answered by {answered_by} — connecting agent")
    return _stream_twiml(call_id)
