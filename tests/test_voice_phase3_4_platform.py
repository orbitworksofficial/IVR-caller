# ============================================================================
# Phase 3 & 4 Platform, Hardening, Monitoring & Voice Admin Tests (Kashif)
#
# Covers:
#   1. CallStore.save_recording: retention expiry calculation, Supabase Storage
#      archiving (storage_path), and purge_expired_recordings()
#   2. CallStore.finalize_call: per-call cost & latency telemetry into usage_events
#   3. CallStore.contact_status_counts: SQL RPC get_campaign_contact_counts + fallback
#   4. VoiceMonitor: call drop alerts, STT/TTS failure alerts, latency P90 SLO breach
#      alerts, and /voice/metrics endpoint
#   5. Gateway rate limiting (429) and max concurrent calls guard (AT_CAPACITY)
#   6. Calendly live single-use scheduling link integration + fallback
#   7. Twilio auto-webhook configuration (configure_number_webhooks)
#   8. Voice Admin API (/admin/voice/*): phone numbers, voice profiles, bot voice
#      config, call history + details, consent, DNC, campaigns, and tenant isolation
# ============================================================================

import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

os.environ["TWILIO_AUTH_TOKEN"] = "test-auth-token"
os.environ["VOICE_PUBLIC_BASE_URL"] = "https://voice.example.com"

from ai.tools.executor import ToolExecutor
from apps.api.admin_auth import get_current_admin
from apps.api.dependencies import get_supabase_admin_client
from apps.api.main import app as api_app
from apps.voice_gateway import twilio_utils
from apps.voice_gateway.call_store import CallStore
from apps.voice_gateway.main import app as voice_app
from apps.voice_gateway.monitoring import SlidingWindowRateLimiter, VoiceMonitor

TENANT_A = "11111111-1111-1111-1111-111111111111"
TENANT_B = "99999999-9999-9999-9999-999999999999"
BOT_A = "22222222-2222-2222-2222-222222222222"
CALL_A = "33333333-3333-3333-3333-333333333333"


# ─────────────────────────────────────────────────────────────────────────────
# 1. Recording Storage, Retention & Expiry Purge
# ─────────────────────────────────────────────────────────────────────────────

class _MockTable:
    def __init__(self, store_dict, name):
        self.store = store_dict
        self.name = name
        self._op = "select"
        self._payload = None
        self._filters = []

    def select(self, *_a, **_kw):
        self._op = "select"
        return self

    def insert(self, payload):
        self._op = "insert"
        self._payload = payload
        return self

    def upsert(self, payload, **_kw):
        self._op = "upsert"
        self._payload = payload
        return self

    def update(self, payload):
        self._op = "update"
        self._payload = payload
        return self

    def delete(self):
        self._op = "delete"
        return self

    def eq(self, col, val):
        self._filters.append(("eq", col, val))
        return self

    def neq(self, col, val):
        self._filters.append(("neq", col, val))
        return self

    def lte(self, col, val):
        self._filters.append(("lte", col, val))
        return self

    def is_(self, col, val):
        self._filters.append(("is", col, val))
        return self

    def order(self, *_a, **_kw):
        return self

    def limit(self, *_a, **_kw):
        return self

    def _matches(self, row):
        for kind, col, val in self._filters:
            rv = row.get(col)
            if kind == "eq" and str(rv) != str(val):
                return False
            if kind == "neq" and str(rv) == str(val):
                return False
            if kind == "lte" and (rv is None or str(rv) > str(val)):
                return False
            if kind == "is" and val == "null" and rv is not None:
                return False
        return True

    def execute(self):
        rows = self.store.setdefault(self.name, [])
        res = MagicMock()
        if self._op == "select":
            res.data = [dict(r) for r in rows if self._matches(r)]
        elif self._op in ("insert", "upsert"):
            items = self._payload if isinstance(self._payload, list) else [self._payload]
            out = []
            for item in items:
                rec = {"id": item.get("id", f"{self.name}-{len(rows)+1}"), **item}
                rows.append(rec)
                out.append(rec)
            res.data = out
        elif self._op == "update":
            out = []
            for r in rows:
                if self._matches(r):
                    r.update(self._payload)
                    out.append(dict(r))
            res.data = out
        elif self._op == "delete":
            kept = [r for r in rows if not self._matches(r)]
            self.store[self.name] = kept
            res.data = []
        return res


class FakeSupabase:
    def __init__(self, initial=None):
        self.tables = initial or {}
        self.uploaded = {}
        self.removed = []
        self.rpc_Handler = None
        self.storage = MagicMock()
        bucket_mock = MagicMock()
        bucket_mock.upload.side_effect = self._upload
        bucket_mock.remove.side_effect = self._remove
        self.storage.from_.return_value = bucket_mock

    def _upload(self, path, data, opts=None):
        self.uploaded[path] = data
        return {"Key": path}

    def _remove(self, paths):
        self.removed.extend(paths)
        return paths

    def table(self, name):
        return _MockTable(self.tables, name)

    def rpc(self, fn_name, params):
        if self.rpc_Handler:
            return self.rpc_Handler(fn_name, params)
        raise RuntimeError("RPC not mocked")


class TestRecordingStorageAndRetention:
    def test_save_recording_uploads_to_storage_and_sets_expiry(self, monkeypatch):
        db = FakeSupabase({
            "calls": [{"id": CALL_A, "tenant_id": TENANT_A, "bot_id": BOT_A, "twilio_call_sid": "CA999"}],
            "ivr_bot_configs": [{"tenant_id": TENANT_A, "bot_id": BOT_A, "recording_retention_days": 30}],
        })
        monkeypatch.setattr(twilio_utils, "download_recording", AsyncMock(return_value=b"FAKE_MP3_BYTES"))

        store = CallStore(db)
        asyncio.run(store.save_recording(
            call_sid="CA999",
            recording_sid="RE123",
            recording_url="https://api.twilio.com/rec/RE123",
            duration_s=42,
            status="completed",
        ))

        recs = db.tables["call_recordings"]
        assert len(recs) == 1
        assert recs[0]["recording_sid"] == "RE123"
        assert recs[0]["storage_path"] == f"call-recordings/{TENANT_A}/{CALL_A}/RE123.mp3"
        assert recs[0]["expires_at"] is not None
        assert db.uploaded[f"{TENANT_A}/{CALL_A}/RE123.mp3"] == b"FAKE_MP3_BYTES"

    def test_purge_expired_recordings_removes_storage_and_marks_expired(self):
        db = FakeSupabase({
            "call_recordings": [
                {
                    "id": "rec-old",
                    "tenant_id": TENANT_A,
                    "call_id": CALL_A,
                    "storage_path": f"call-recordings/{TENANT_A}/{CALL_A}/RE_OLD.mp3",
                    "recording_url": "https://api.twilio.com/rec/RE_OLD",
                    "status": "completed",
                    "expires_at": "2020-01-01T00:00:00+00:00",
                },
                {
                    "id": "rec-fresh",
                    "tenant_id": TENANT_A,
                    "call_id": CALL_A,
                    "storage_path": f"call-recordings/{TENANT_A}/{CALL_A}/RE_FRESH.mp3",
                    "status": "completed",
                    "expires_at": "2099-01-01T00:00:00+00:00",
                },
            ]
        })
        store = CallStore(db)
        purged = asyncio.run(store.purge_expired_recordings("2026-10-16T00:00:00+00:00"))
        assert purged == 1
        assert db.removed == [f"{TENANT_A}/{CALL_A}/RE_OLD.mp3"]
        old_row = next(r for r in db.tables["call_recordings"] if r["id"] == "rec-old")
        fresh_row = next(r for r in db.tables["call_recordings"] if r["id"] == "rec-fresh")
        assert old_row["status"] == "expired" and old_row["storage_path"] is None
        assert fresh_row["status"] == "completed"


# ─────────────────────────────────────────────────────────────────────────────
# 2. Per-Call Cost Telemetry into usage_events & SQL RPC Campaign Counts
# ─────────────────────────────────────────────────────────────────────────────

class TestFinalizeCallAndCampaignRpc:
    def test_finalize_call_inserts_usage_event(self):
        db = FakeSupabase({
            "calls": [{"id": CALL_A, "tenant_id": TENANT_A, "direction": "inbound"}],
        })
        store = CallStore(db)
        turns = [
            {"speaker": "caller", "text": "What are your hours?", "latency_ms": None},
            {"speaker": "agent", "text": "We are open 9 to 6 Monday through Friday.", "latency_ms": 480},
        ]
        asyncio.run(store.finalize_call(
            call_id=CALL_A,
            tenant_id=TENANT_A,
            direction="inbound",
            turns=turns,
            summary={"summary": "Asked about business hours."},
            stt_seconds=12.5,
            duration_s=30,
            model_used="gpt-5.6-luna",
        ))

        events = db.tables.get("usage_events", [])
        assert len(events) == 1
        ev = events[0]
        assert ev["tenant_id"] == TENANT_A
        assert ev["call_id"] == CALL_A
        assert ev["model"] == "gpt-5.6-luna"
        assert ev["telephony_minutes"] == 0.5
        assert ev["stt_seconds"] == 12.5
        assert ev["tts_characters"] == len(turns[1]["text"])
        assert ev["latency_ms"] == 480
        assert ev["cost_usd"] > 0

    def test_contact_status_counts_uses_rpc_and_falls_back(self):
        db = FakeSupabase({
            "campaign_contacts": [
                {"campaign_id": "camp-1", "status": "pending"},
                {"campaign_id": "camp-1", "status": "done"},
                {"campaign_id": "camp-1", "status": "done"},
            ]
        })
        store = CallStore(db)
        # Fallback path when RPC raises
        counts_fallback = asyncio.run(store.contact_status_counts("camp-1"))
        assert counts_fallback == {"pending": 1, "done": 2}

        # RPC path when available
        def fake_rpc(fn_name, params):
            assert fn_name == "get_campaign_contact_counts"
            m = MagicMock()
            m.execute.return_value = MagicMock(data=[
                {"status": "pending", "count": 5},
                {"status": "done", "count": 12},
            ])
            return m

        db.rpc_Handler = fake_rpc
        counts_rpc = asyncio.run(store.contact_status_counts("camp-1"))
        assert counts_rpc == {"pending": 5, "done": 12}


# ─────────────────────────────────────────────────────────────────────────────
# 3. Monitoring, Alerting, Rate Limiting & Concurrency Guard
# ─────────────────────────────────────────────────────────────────────────────

class TestMonitoringAndGatewayProtection:
    def test_monitor_tracks_slo_breaches_drops_and_failures(self):
        mon = VoiceMonitor(slo_p90_ms=800)
        mon.call_started("c1", TENANT_A)
        mon.record_turn_latency("c1", 450)
        mon.record_turn_latency("c1", 1150)  # Breach!
        mon.record_stt_failure("c1", "deepgram timeout")
        mon.record_tts_failure("c1", "elevenlabs 502")
        mon.call_ended("c1", dropped=True, reason="websocket_disconnect")

        snap = mon.snapshot()
        assert snap["active_calls"] == 0
        assert snap["total_calls"] == 1
        assert snap["dropped_calls"] == 1
        assert snap["stt_failures"] == 1
        assert snap["tts_failures"] == 1
        assert snap["slo_breaches"] == 1
        assert snap["slo_healthy"] is False
        kinds = [a["kind"] for a in snap["recent_alerts"]]
        assert "latency_slo_breach" in kinds
        assert "stt_failure" in kinds
        assert "tts_failure" in kinds
        assert "call_dropped" in kinds

    def test_voice_metrics_endpoint(self):
        voice_app.state.monitor = VoiceMonitor(slo_p90_ms=1000)
        voice_app.state.monitor.record_turn_latency("c-test", 520)
        client = TestClient(voice_app)
        r = client.get("/voice/metrics")
        assert r.status_code == 200
        body = r.json()
        assert body["latency_p50_ms"] == 520
        assert body["slo_healthy"] is True

    def test_inbound_rate_limiting_and_concurrency_cap(self, monkeypatch):
        from tests.test_voice_gateway import FakeStore
        voice_app.state.store = FakeStore()
        voice_app.state.monitor = VoiceMonitor()
        voice_app.state.rate_limiter = SlidingWindowRateLimiter(max_requests=2, window_seconds=60)
        client = TestClient(voice_app)

        form = {"CallSid": "CA1", "From": "+15551112222", "To": "+15550001111"}
        sig = twilio_utils.compute_signature(
            "test-auth-token", "https://voice.example.com/voice/inbound", form
        )
        headers = {"X-Twilio-Signature": sig}

        try:
            # Max concurrent calls guard
            monkeypatch.setenv("VOICE_MAX_CONCURRENT_CALLS", "1")
            voice_app.state.monitor.active_calls = 1
            r_cap = client.post("/voice/inbound", data=form, headers=headers)
            assert r_cap.status_code == 200
            assert "currently busy" in r_cap.text

            # Rate limiter (3rd request within window gets 429)
            voice_app.state.monitor.active_calls = 0
            client.post("/voice/inbound", data=form, headers=headers)
            r_429 = client.post("/voice/inbound", data=form, headers=headers)
            assert r_429.status_code == 429
        finally:
            voice_app.state.store = None
            voice_app.state.rate_limiter = SlidingWindowRateLimiter(max_requests=120, window_seconds=60)


# ─────────────────────────────────────────────────────────────────────────────
# 4. Calendly Live Single-Use Link + Twilio Webhook Auto-Configuration
# ─────────────────────────────────────────────────────────────────────────────

class TestCalendlyAndTwilioIntegrations:
    def test_book_appointment_uses_live_calendly_when_configured(self, monkeypatch):
        db = FakeSupabase({
            "bots": [{"id": BOT_A, "tenant_id": TENANT_A, "name": "Orbit", "booking_url": "https://calendly.com/fallback"}]
        })
        monkeypatch.setenv("CALENDLY_API_KEY", "cal-key")
        monkeypatch.setenv("CALENDLY_EVENT_TYPE_URI", "https://api.calendly.com/event_types/ET1")

        executor = ToolExecutor(db, TENANT_A, BOT_A, call_id=CALL_A)
        monkeypatch.setattr(
            executor,
            "_create_calendly_single_use_link",
            AsyncMock(return_value="https://calendly.com/d/single-use-xyz"),
        )
        res = asyncio.run(executor.execute("book_appointment", {"contact_name": "Alice"}))
        assert res["status"] == "booking_link_provided"
        assert res["booking_url"] == "https://calendly.com/d/single-use-xyz"

    def test_configure_number_webhooks_updates_twilio_incoming_number(self, monkeypatch):
        monkeypatch.setenv("TWILIO_ACCOUNT_SID", "AC_TEST")
        monkeypatch.setenv("TWILIO_AUTH_TOKEN", "test-auth-token")
        monkeypatch.setenv("VOICE_PUBLIC_BASE_URL", "https://voice.example.com")

        calls_made = []

        class FakeAsyncClient:
            def __init__(self, **_kw):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_a):
                pass

            async def get(self, url, params=None, auth=None):
                calls_made.append(("GET", url, params))
                r = MagicMock(status_code=200)
                r.json.return_value = {"incoming_phone_numbers": [{"sid": "PN12345"}]}
                return r

            async def post(self, url, data=None, auth=None):
                calls_made.append(("POST", url, data))
                return MagicMock(status_code=200, text="ok")

        monkeypatch.setattr(twilio_utils.httpx, "AsyncClient", FakeAsyncClient)
        result = asyncio.run(twilio_utils.configure_number_webhooks("+14435550199"))
        assert result == {
            "sid": "PN12345",
            "voice_url": "https://voice.example.com/voice/inbound",
            "status_callback": "https://voice.example.com/voice/status",
        }
        assert calls_made[1][0] == "POST"
        assert calls_made[1][2]["VoiceUrl"] == "https://voice.example.com/voice/inbound"


# ─────────────────────────────────────────────────────────────────────────────
# 5. Voice Admin API Endpoints & Multi-Tenant Isolation
# ─────────────────────────────────────────────────────────────────────────────

class TestVoiceAdminApi:
    @pytest.fixture
    def admin_env(self, monkeypatch):
        db = FakeSupabase({
            "bots": [
                {"id": BOT_A, "tenant_id": TENANT_A, "name": "Orbit Bot"},
                {"id": "bot-b", "tenant_id": TENANT_B, "name": "Other Tenant Bot"},
            ],
            "calls": [
                {
                    "id": CALL_A,
                    "tenant_id": TENANT_A,
                    "bot_id": BOT_A,
                    "direction": "inbound",
                    "from_number": "+14435550100",
                    "to_number": "+14435550199",
                    "status": "completed",
                    "started_at": "2026-10-16T10:00:00Z",
                },
                {
                    "id": "call-b",
                    "tenant_id": TENANT_B,
                    "bot_id": "bot-b",
                    "direction": "inbound",
                    "from_number": "+19998887777",
                    "to_number": "+19990001111",
                    "status": "completed",
                    "started_at": "2026-10-16T10:00:00Z",
                },
            ],
            "call_turns": [
                {"id": "t1", "call_id": CALL_A, "tenant_id": TENANT_A, "speaker": "agent", "text": "Hello!", "turn_index": 0}
            ],
            "call_transcripts": [
                {"id": "tr1", "call_id": CALL_A, "tenant_id": TENANT_A, "full_transcript": "Agent: Hello!", "word_count": 2}
            ],
            "call_recordings": [],
        })

        monkeypatch.setattr(twilio_utils, "configure_number_webhooks", AsyncMock(return_value={
            "sid": "PN_AUTO",
            "voice_url": "https://voice.example.com/voice/inbound",
            "status_callback": "https://voice.example.com/voice/status",
        }))

        api_app.dependency_overrides[get_current_admin] = lambda: {
            "user_id": "u1",
            "email": "admin@orbit.com",
            "tenant_id": TENANT_A,
            "own_tenant_id": TENANT_A,
            "role": "admin",
            "is_super_admin": False,
            "is_impersonating": False,
        }
        api_app.dependency_overrides[get_supabase_admin_client] = lambda: db
        yield TestClient(api_app), db
        api_app.dependency_overrides.clear()

    def test_phone_numbers_crud_and_cross_tenant_conflict(self, admin_env):
        client, db = admin_env
        # Seed a number owned by Tenant B
        db.tables["phone_numbers"] = [
            {"id": "pn-b", "tenant_id": TENANT_B, "phone_number": "+18005559999", "is_active": True}
        ]

        # Tenant A cannot claim Tenant B's active number
        r_conflict = client.post("/admin/voice/numbers", json={"phone_number": "+18005559999"})
        assert r_conflict.status_code == 409

        # Tenant A adds its own number
        r_ok = client.post("/admin/voice/numbers", json={
            "phone_number": "+14435550199",
            "bot_id": BOT_A,
            "direction": "both",
        })
        assert r_ok.status_code == 201
        assert r_ok.json()["twilio_webhooks"]["sid"] == "PN_AUTO"

        # Listing only returns Tenant A's numbers
        r_list = client.get("/admin/voice/numbers")
        assert r_list.status_code == 200
        assert [n["phone_number"] for n in r_list.json()] == ["+14435550199"]

    def test_bot_voice_config_validates_and_saves_script(self, admin_env):
        client, _ = admin_env
        # Invalid voice_script key rejected
        r_bad = client.put(f"/admin/voice/bots/{BOT_A}/config", json={
            "voice_script": {"unknown_key": "bad"}
        })
        assert r_bad.status_code == 400

        # Valid config + voice_script saved
        r_good = client.put(f"/admin/voice/bots/{BOT_A}/config", json={
            "calling_timezone": "America/New_York",
            "recording_retention_days": 60,
            "voice_script": {
                "greetings": {
                    "inbound_support": "Hello! This is the automated AI assistant for Orbit Works.",
                },
                "extra_rules": ["Always offer a discovery call."],
            },
        })
        assert r_good.status_code == 200
        assert r_good.json()["recording_retention_days"] == 60

    def test_calls_consent_dnc_and_campaigns_isolated_to_tenant(self, admin_env):
        client, _ = admin_env
        # Call list excludes Tenant B's call
        calls = client.get("/admin/voice/calls").json()
        assert [c["id"] for c in calls] == [CALL_A]

        # Fetching Tenant B's call by ID returns 404
        assert client.get("/admin/voice/calls/call-b").status_code == 404

        # Fetching Tenant A's call returns turns & transcript
        detail = client.get(f"/admin/voice/calls/{CALL_A}").json()
        assert detail["call"]["id"] == CALL_A
        assert len(detail["turns"]) == 1

        # Grant consent & add to DNC
        c_res = client.post("/admin/voice/consent", json={"phone_number": "+14435550123"})
        assert c_res.status_code == 201
        d_res = client.post("/admin/voice/dnc", json={"phone_number": "+14435550123", "reason": "Requested opt-out"})
        assert d_res.status_code == 201

        # Create & activate campaign
        camp = client.post("/admin/voice/campaigns", json={
            "name": "October Leads",
            "bot_id": BOT_A,
            "call_reason": "Follow up on demo request",
            "phone_numbers": ["+14435550155"],
        })
        assert camp.status_code == 201
        camp_id = camp.json()["id"]

        patched = client.patch(f"/admin/voice/campaigns/{camp_id}", json={"status": "active"})
        assert patched.status_code == 200
        assert patched.json()["status"] == "active"
