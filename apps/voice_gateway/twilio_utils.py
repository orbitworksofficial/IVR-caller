"""
Twilio request verification, TwiML builders and the minimal REST calls the
gateway needs. Implemented directly against Twilio's documented algorithms so
the image does not need the full twilio SDK.
"""

import base64
import hashlib
import hmac
import logging
import os
from typing import Dict, Mapping, Optional
from xml.sax.saxutils import escape, quoteattr

import httpx

logger = logging.getLogger(__name__)

TWILIO_API_BASE = "https://api.twilio.com/2010-04-01"


def auth_token() -> str:
    return os.getenv("TWILIO_AUTH_TOKEN", "")


def public_base_url() -> str:
    """Public https origin Twilio uses to reach us, e.g. https://chat.orb-itworks.com"""
    return os.getenv("VOICE_PUBLIC_BASE_URL", "").rstrip("/")


def signature_validation_enabled() -> bool:
    return os.getenv("VOICE_VALIDATE_TWILIO_SIGNATURE", "true").lower() != "false"


# ── Webhook signature ─────────────────────────────────────────────────────────

def compute_signature(token: str, url: str, params: Mapping[str, str]) -> str:
    """
    X-Twilio-Signature: base64(HMAC-SHA1(token, url + concat(sorted(key+value)))).
    `url` must be exactly the URL Twilio requested — behind Nginx that is the
    public URL, not the one uvicorn sees, which is why it is rebuilt from
    VOICE_PUBLIC_BASE_URL rather than taken from the request.
    """
    payload = url + "".join(f"{k}{params[k]}" for k in sorted(params))
    digest = hmac.new(token.encode(), payload.encode(), hashlib.sha1).digest()
    return base64.b64encode(digest).decode()


def is_valid_signature(token: str, url: str, params: Mapping[str, str], signature: Optional[str]) -> bool:
    if not token or not signature:
        return False
    return hmac.compare_digest(compute_signature(token, url, params), signature)


# ── Media-stream token ────────────────────────────────────────────────────────
# The WebSocket carries no form body to sign, so the inbound webhook embeds a
# token derived from the call id in the TwiML <Parameter>s. Twilio echoes it
# back in the stream's "start" event; it is stateless, so any worker can verify.

def stream_token(call_id: str) -> str:
    key = (auth_token() or "dev-only-insecure-key").encode()
    return hmac.new(key, f"stream:{call_id}".encode(), hashlib.sha256).hexdigest()


def is_valid_stream_token(call_id: str, token: Optional[str]) -> bool:
    return bool(call_id and token) and hmac.compare_digest(stream_token(call_id), token)


# ── TwiML ─────────────────────────────────────────────────────────────────────

def twiml_connect_stream(stream_url: str, parameters: Dict[str, str]) -> str:
    """
    <Connect><Stream> (not <Start>) — Connect makes the stream bidirectional,
    which is what lets us play TTS audio back into the call.
    """
    params = "".join(
        f"<Parameter name={quoteattr(k)} value={quoteattr(v)} />" for k, v in parameters.items()
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f"<Response><Connect><Stream url={quoteattr(stream_url)}>{params}</Stream></Connect></Response>"
    )


def twiml_say_hangup(message: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f"<Response><Say>{escape(message)}</Say><Hangup/></Response>"
    )


# ── REST ──────────────────────────────────────────────────────────────────────

class TwilioError(Exception):
    pass


async def create_outbound_call(to_number: str, from_number: str, answer_url: str, status_url: str) -> str:
    """
    Places an outbound call and returns its CallSid.

    MachineDetection=DetectMessageEnd: Twilio holds the answer webhook until it
    knows who picked up, and for a machine waits for the greeting to finish so
    a voicemail can be left after the beep. The result arrives as AnsweredBy.
    """
    account_sid = os.getenv("TWILIO_ACCOUNT_SID", "")
    if not account_sid or not auth_token():
        raise TwilioError("TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN not set")

    data = {
        "To": to_number,
        "From": from_number,
        "Url": answer_url,
        "Method": "POST",
        "StatusCallback": status_url,
        "StatusCallbackMethod": "POST",
        "StatusCallbackEvent": ["initiated", "ringing", "answered", "completed"],
        "MachineDetection": "DetectMessageEnd",
        "Timeout": "25",
    }
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{TWILIO_API_BASE}/Accounts/{account_sid}/Calls.json",
            data=data, auth=(account_sid, auth_token()),
        )
    if resp.status_code >= 300:
        raise TwilioError(f"Twilio create-call failed ({resp.status_code}): {resp.text}")
    return resp.json()["sid"]


def twiml_voicemail(message: Optional[str]) -> str:
    """Approved voicemail message (if the tenant configured one), then hang up."""
    say = f"<Pause length=\"1\"/><Say>{escape(message)}</Say>" if message else ""
    return f'<?xml version="1.0" encoding="UTF-8"?><Response>{say}<Hangup/></Response>'


async def start_call_recording(call_sid: str, status_callback_url: str) -> Optional[str]:
    """
    Starts a dual-channel recording on a live call. Recordings stay in Twilio
    storage; the status callback stores the reference in call_recordings.
    Returns the RecordingSid, or None if recording could not be started.
    """
    account_sid = os.getenv("TWILIO_ACCOUNT_SID", "")
    if not account_sid or not auth_token():
        logger.warning("Twilio credentials missing — call will not be recorded.")
        return None

    url = f"{TWILIO_API_BASE}/Accounts/{account_sid}/Calls/{call_sid}/Recordings.json"
    data = {
        "RecordingChannels": "dual",
        "RecordingStatusCallback": status_callback_url,
        "RecordingStatusCallbackEvent": "completed",
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, data=data, auth=(account_sid, auth_token()))
        if resp.status_code >= 300:
            logger.error(f"Twilio start-recording failed ({resp.status_code}): {resp.text}")
            return None
        return resp.json().get("sid")
    except Exception as e:
        logger.error(f"Twilio start-recording error: {e}")
        return None


async def transfer_call(call_sid: str, to_number: str, message: Optional[str] = None) -> bool:
    """
    Redirects a live Twilio call to a new phone number (warm transfer).
    Updates the in-progress call with TwiML containing <Say> and <Dial>.
    """
    account_sid = os.getenv("TWILIO_ACCOUNT_SID", "")
    if not account_sid or not auth_token():
        logger.warning("Twilio credentials missing — cannot execute warm transfer.")
        return False

    say_xml = f"<Say>{escape(message)}</Say>" if message else ""
    twiml = f'<?xml version="1.0" encoding="UTF-8"?><Response>{say_xml}<Dial>{escape(to_number)}</Dial></Response>'
    url = f"{TWILIO_API_BASE}/Accounts/{account_sid}/Calls/{call_sid}.json"

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, data={"Twiml": twiml}, auth=(account_sid, auth_token()))
        if resp.status_code >= 300:
            logger.error(f"Twilio warm transfer failed ({resp.status_code}): {resp.text}")
            return False
        logger.info(f"Twilio warm transfer executed for call {call_sid} -> {to_number}")
        return True
    except Exception as e:
        logger.error(f"Twilio warm transfer error for call {call_sid}: {e}")
        return False

