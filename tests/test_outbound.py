# ============================================================================
# Outbound dialing + compliance tests (Kashif — Phase 2)
#
# Covers:
#   - Dial policy rules: E.164, DNC, consent, calling window (tz, overnight),
#     attempt limit, retry interval, fail-closed on errors/misconfiguration
#   - place_outbound_call: blocked dials never reach Twilio and are logged;
#     allowed dials create calls + dial_attempts rows and request AMD
#   - /voice/outbound/dial auth, /voice/outbound-answer human vs voicemail
#   - Verbal opt-out on a live call → DNC, even with an instant hang-up
#
# Run: pytest tests/test_outbound.py -v
# ============================================================================

import asyncio
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

os.environ["TWILIO_AUTH_TOKEN"] = "test-auth-token"
os.environ["VOICE_PUBLIC_BASE_URL"] = "https://voice.example.com"

from apps.voice_gateway import outbound as outbound_mod
from apps.voice_gateway import twilio_utils
from apps.voice_gateway.dial_policy import check_dial, evaluate, in_calling_window
from apps.voice_gateway.main import app
from apps.voice_gateway.opt_out import is_opt_out
from apps.voice_gateway.outbound import place_outbound_call

from tests.test_voice_gateway import (
    BOT, CALL, TENANT, FakeBaseProvider, FakeTTS, SILENCE, _media_msg, _start_msg, make_factory,
)

NUMBER = "+14435550123"
ALL_DAY = {"calling_window_start": "00:00:00", "calling_window_end": "23:59:59", "calling_timezone": "UTC"}
# 15:00 UTC = 11:00 in New York (EDT) = 20:00 in Karachi
NOW = datetime(2026, 10, 15, 15, 0, tzinfo=timezone.utc)


def _eval(**kw):
    args = dict(phone_number=NUMBER, now_utc=NOW, on_dnc=False, has_consent=True,
                config=dict(ALL_DAY), previous_attempts=[])
    args.update(kw)
    return evaluate(**args)


# ─────────────────────────────────────────────────────────────────────────────
# Policy rules
# ─────────────────────────────────────────────────────────────────────────────

class TestDialPolicy:
    def test_allowed(self):
        d = _eval()
        assert d.allowed and d.reason == "ok" and d.attempt_outcome is None

    @pytest.mark.parametrize("number", ["4435550123", "+0123456789", "+1 443 555 0123", "", "+1"])
    def test_invalid_numbers_blocked(self, number):
        assert _eval(phone_number=number).reason == "invalid_number"

    def test_dnc_blocks_even_with_consent(self):
        d = _eval(on_dnc=True, has_consent=True)
        assert not d.allowed and d.reason == "dnc" and d.attempt_outcome == "dnc_blocked"

    def test_no_consent_blocks(self):
        d = _eval(has_consent=False)
        assert d.reason == "consent" and d.attempt_outcome == "consent_blocked"

    def test_window_uses_tenant_timezone(self):
        ny = {"calling_window_start": "09:00", "calling_window_end": "18:00", "calling_timezone": "America/New_York"}
        karachi = {**ny, "calling_timezone": "Asia/Karachi"}
        assert _eval(config=ny).allowed                      # 11:00 in New York
        assert _eval(config=karachi).reason == "window"      # 20:00 in Karachi
        assert _eval(config=karachi).attempt_outcome == "window_blocked"

    def test_window_crossing_midnight(self):
        assert in_calling_window(NOW.replace(hour=23), "22:00", "02:00", "UTC")
        assert in_calling_window(NOW.replace(hour=1), "22:00", "02:00", "UTC")
        assert not in_calling_window(NOW.replace(hour=12), "22:00", "02:00", "UTC")

    def test_window_end_is_exclusive(self):
        assert not in_calling_window(NOW.replace(hour=18, minute=0), "09:00", "18:00", "UTC")

    def test_default_window_when_unconfigured(self):
        # No config at all → 09:00–18:00 America/New_York; 15:00 UTC is 11:00 there.
        assert _eval(config={}).allowed
        assert _eval(config={}, now_utc=NOW.replace(hour=3)).reason == "window"

    def test_bad_timezone_fails_closed(self):
        d = _eval(config={**ALL_DAY, "calling_timezone": "Mars/Olympus"})
        assert not d.allowed and d.reason == "window"

    def test_attempt_limit(self):
        old = [{"dialed_at": (NOW - timedelta(days=5)).isoformat()}] * 3
        assert _eval(previous_attempts=old).reason == "attempts"
        assert _eval(previous_attempts=old[:2]).allowed
        # Campaign limit overrides the bot default.
        assert _eval(previous_attempts=old[:2], max_attempts=2).reason == "attempts"

    def test_retry_interval(self):
        recent = [{"dialed_at": (NOW - timedelta(hours=3)).isoformat()}]
        assert _eval(previous_attempts=recent).reason == "retry"
        assert _eval(previous_attempts=recent, config={**ALL_DAY, "retry_interval_hours": 2}).allowed

    def test_reason_order_dnc_beats_window(self):
        d = _eval(on_dnc=True, config={**ALL_DAY, "calling_timezone": "Mars/Olympus"})
        assert d.reason == "dnc"


class ExplodingStore:
    async def load_ivr_config(self, *a):
        raise RuntimeError("db down")


def test_check_dial_fails_closed_on_db_error():
    d = asyncio.run(check_dial(ExplodingStore(), TENANT, BOT, NUMBER))
    assert not d.allowed and d.reason == "check_error"


# ─────────────────────────────────────────────────────────────────────────────
# Dialer
# ─────────────────────────────────────────────────────────────────────────────

class OutboundStore:
    def __init__(self, dnc=False, consent=True, config=None, attempts=None, campaign=None):
        self.dnc, self.consent = dnc, consent
        self.config = dict(ALL_DAY) if config is None else config
        self.attempts = attempts or []
        self.campaign = campaign
        self.recorded, self.calls, self.call_updates, self.attempt_updates = [], {}, [], []
        self.opt_outs = []

    async def get_campaign(self, tenant_id, campaign_id):
        return self.campaign

    async def find_outbound_number(self, tenant_id, bot_id):
        return {"id": "pn-1", "phone_number": "+15550001111"}

    async def load_ivr_config(self, tenant_id, bot_id):
        return self.config

    async def is_on_dnc(self, tenant_id, number):
        return self.dnc

    async def has_active_consent(self, tenant_id, number, kind):
        return self.consent

    async def list_dial_attempts(self, tenant_id, number, campaign_id):
        return self.attempts

    async def record_dial_attempt(self, row):
        self.recorded.append(row)
        return row

    async def create_call(self, **kw):
        row = {"id": CALL, **kw}
        self.calls[CALL] = row
        return row

    async def get_call(self, call_id):
        return self.calls.get(call_id)

    async def update_call(self, call_id, tenant_id, update):
        self.call_updates.append(update)
        self.calls.get(call_id, {}).update(update)

    async def update_dial_attempt_for_call(self, call_id, update):
        self.attempt_updates.append(update)

    async def get_call_reason(self, call_id, tenant_id):
        return "Follow up on website enquiry"

    async def mark_stream_started(self, *a):
        pass

    async def opt_out(self, tenant_id, number, call_id, evidence):
        await asyncio.sleep(0.05)  # slower than the hang-up, on purpose
        self.opt_outs.append((tenant_id, number, evidence))

    async def finalize_call(self, **kw):
        self.finalized = kw


@pytest.fixture
def twilio_calls(monkeypatch):
    placed = []

    async def fake_create(**kw):
        placed.append(kw)
        return "CA-outbound-1"

    monkeypatch.setattr(outbound_mod.twilio_utils, "create_outbound_call", fake_create)
    return placed


class TestPlaceOutboundCall:
    def test_blocked_dial_never_reaches_twilio(self, twilio_calls):
        store = OutboundStore(dnc=True)
        r = asyncio.run(place_outbound_call(store, TENANT, BOT, NUMBER))
        assert not r.dialed and r.reason == "dnc"
        assert twilio_calls == [] and store.calls == {}
        assert store.recorded[0]["outcome"] == "dnc_blocked"
        assert "dialed_at" not in store.recorded[0]

    def test_allowed_dial(self, twilio_calls):
        store = OutboundStore(attempts=[{"dialed_at": "2026-01-01T00:00:00+00:00"}])
        r = asyncio.run(place_outbound_call(store, TENANT, BOT, NUMBER, lead_id="lead-1"))
        assert r.dialed and r.call_sid == "CA-outbound-1" and r.call_id == CALL
        assert store.calls[CALL]["direction"] == "outbound"
        assert store.calls[CALL]["from_number"] == "+15550001111"
        assert store.calls[CALL]["twilio_call_sid"] == "CA-outbound-1"
        attempt = store.recorded[0]
        assert attempt["attempt_number"] == 2 and attempt["dialed_at"] and attempt["lead_id"] == "lead-1"
        assert twilio_calls[0]["answer_url"] == f"https://voice.example.com/voice/outbound-answer?call_id={CALL}"

    def test_inactive_campaign_not_dialed(self, twilio_calls):
        store = OutboundStore(campaign={"id": "c1", "status": "paused"})
        r = asyncio.run(place_outbound_call(store, TENANT, BOT, NUMBER, campaign_id="c1"))
        assert r.reason == "campaign_not_active" and twilio_calls == []

    def test_twilio_failure_marks_call_failed(self, monkeypatch):
        async def boom(**kw):
            raise twilio_utils.TwilioError("401 auth")
        monkeypatch.setattr(outbound_mod.twilio_utils, "create_outbound_call", boom)
        store = OutboundStore()
        r = asyncio.run(place_outbound_call(store, TENANT, BOT, NUMBER))
        assert not r.dialed and r.reason == "twilio_error"
        assert store.calls[CALL]["status"] == "failed"
        assert store.attempt_updates[-1]["outcome"] == "failed"


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def ostore():
    s = OutboundStore()
    app.state.store = s
    yield s
    app.state.store = None


class TestDialEndpoint:
    body = {"tenant_id": TENANT, "bot_id": BOT, "to_number": NUMBER}

    def test_disabled_without_key(self, ostore, monkeypatch):
        monkeypatch.delenv("VOICE_DIAL_API_KEY", raising=False)
        assert TestClient(app).post("/voice/outbound/dial", json=self.body).status_code == 503

    def test_wrong_key(self, ostore, monkeypatch):
        monkeypatch.setenv("VOICE_DIAL_API_KEY", "right")
        r = TestClient(app).post("/voice/outbound/dial", json=self.body, headers={"X-Voice-Api-Key": "wrong"})
        assert r.status_code == 401

    def test_dials(self, ostore, monkeypatch, twilio_calls):
        monkeypatch.setenv("VOICE_DIAL_API_KEY", "right")
        r = TestClient(app).post("/voice/outbound/dial", json=self.body, headers={"X-Voice-Api-Key": "right"})
        assert r.status_code == 200 and r.json()["dialed"] is True


class TestOutboundAnswer:
    def _post(self, answered_by):
        url = f"https://voice.example.com/voice/outbound-answer?call_id={CALL}"
        form = {"CallSid": "CA-outbound-1", "AnsweredBy": answered_by}
        sig = twilio_utils.compute_signature("test-auth-token", url, form)
        return TestClient(app).post(
            f"/voice/outbound-answer?call_id={CALL}", data=form, headers={"X-Twilio-Signature": sig}
        )

    def _seed(self, store):
        store.calls[CALL] = {"id": CALL, "tenant_id": TENANT, "bot_id": BOT,
                             "twilio_call_sid": "CA-outbound-1", "direction": "outbound"}

    def test_human_connects_agent(self, ostore):
        self._seed(ostore)
        r = self._post("human")
        assert "<Connect><Stream" in r.text
        assert ostore.attempt_updates[-1] == {"outcome": "completed"}

    def test_voicemail_leaves_approved_message(self, ostore):
        self._seed(ostore)
        ostore.config = {"voicemail_message": "Hi, this is OrbitWorks returning your enquiry."}
        r = self._post("machine_end_beep")
        assert "<Connect>" not in r.text
        assert "returning your enquiry" in r.text and "<Hangup/>" in r.text
        assert ostore.calls[CALL]["outcome"] == "voicemail"
        assert ostore.attempt_updates[-1] == {"outcome": "voicemail"}

    def test_voicemail_without_message_hangs_up(self, ostore):
        self._seed(ostore)
        r = self._post("machine_end_silence")
        assert "<Say>" not in r.text and "<Hangup/>" in r.text

    def test_unsigned_rejected(self, ostore):
        self._seed(ostore)
        r = TestClient(app).post(f"/voice/outbound-answer?call_id={CALL}", data={"AnsweredBy": "human"})
        assert r.status_code == 403


# ─────────────────────────────────────────────────────────────────────────────
# Verbal opt-out
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "Please remove me from your list.",
    "Don't call me again",
    "don’t call this number",
    "Stop calling me!",
    "Take me off your calling list",
    "put me on your do not call list",
    "I want to unsubscribe",
])
def test_opt_out_detected(text):
    assert is_opt_out(text)


@pytest.mark.parametrize("text", [
    "What are your business hours?",
    "Can you call me back tomorrow?",
    "I'd like to remove the old website and build a new one",
    "Please call me on my mobile instead",
])
def test_opt_out_not_triggered(text):
    assert not is_opt_out(text)


def test_opt_out_on_live_call_survives_instant_hangup(ostore):
    ostore.calls[CALL] = {"id": CALL, "tenant_id": TENANT, "bot_id": BOT, "direction": "outbound",
                          "from_number": "+15550001111", "to_number": NUMBER}
    app.state.components_factory = make_factory(
        FakeBaseProvider(), FakeTTS(), "Please remove me from your list.", "x"
    )
    with TestClient(app).websocket_connect("/voice/stream") as ws:
        ws.send_text(_start_msg())
        ws.receive_text()                       # greeting
        ws.send_text(_media_msg(SILENCE))
        import time; time.sleep(0.3)            # STT final lands, then caller hangs up
        ws.send_text(json.dumps({"event": "stop"}))

    # Outbound: the number we dialled (to_number) is the one that opted out.
    assert ostore.opt_outs and ostore.opt_outs[0][:2] == (TENANT, NUMBER)
