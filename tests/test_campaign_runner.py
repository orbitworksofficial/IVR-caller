# ============================================================================
# Campaign runner tests (Kashif — Phase 2)
#
# Covers:
#   - Contact state transitions after a dial (placed / blocked / transient)
#   - Contact state transitions after a call ends (answered / retry / exhausted / stale)
#   - Full runner ticks: concurrency limit, reconcile, retry scheduling,
#     permanent blocks, lost claim races, campaign completion
#
# Run: pytest tests/test_campaign_runner.py -v
# ============================================================================

import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from apps.voice_gateway.campaign_runner import CampaignRunner, after_call, after_dial
from apps.voice_gateway.outbound import DialResult

NOW = datetime(2026, 10, 15, 15, 0, tzinfo=timezone.utc)
TENANT, BOT, CAMP = "t1", "b1", "c1"


def _ts(dt):
    return datetime.fromisoformat(dt)


class TestAfterDial:
    def test_dialed(self):
        u = after_dial(DialResult(True, "ok", call_id="call-1"), {"attempts": 1}, 24, NOW)
        assert u == {"status": "dialing", "current_call_id": "call-1", "attempts": 2, "last_outcome": "dialing"}

    def test_permanent_blocks(self):
        for reason in ("dnc", "consent", "invalid_number"):
            assert after_dial(DialResult(False, reason), {}, 24, NOW)["status"] == "blocked"

    def test_attempts_exhausted(self):
        assert after_dial(DialResult(False, "attempts"), {}, 24, NOW)["status"] == "exhausted"

    def test_window_rechecks_soon(self):
        u = after_dial(DialResult(False, "window"), {"attempts": 0}, 24, NOW)
        assert u["status"] == "pending" and _ts(u["next_attempt_at"]) == NOW + timedelta(minutes=15)
        assert "attempts" not in u

    def test_twilio_error_counts_attempt(self):
        u = after_dial(DialResult(False, "twilio_error"), {"attempts": 1}, 24, NOW)
        assert u["status"] == "pending" and u["attempts"] == 2
        assert _ts(u["next_attempt_at"]) == NOW + timedelta(minutes=5)


class TestAfterCall:
    contact = {"attempts": 1, "claimed_at": (NOW - timedelta(minutes=3)).isoformat()}

    def test_live_call_waits(self):
        assert after_call("completed", "in-progress", self.contact, 3, 24, NOW) is None
        assert after_call(None, "ringing", self.contact, 3, 24, NOW) is None

    def test_answered_is_done(self):
        assert after_call("completed", "completed", self.contact, 3, 24, NOW)["status"] == "done"

    def test_voicemail_retries_after_interval(self):
        u = after_call("voicemail", "completed", self.contact, 3, 24, NOW)
        assert u["status"] == "pending" and u["last_outcome"] == "voicemail"
        assert _ts(u["next_attempt_at"]) == NOW + timedelta(hours=24)

    def test_no_answer_on_last_attempt_exhausts(self):
        u = after_call("no_answer", "no-answer", {**self.contact, "attempts": 3}, 3, 24, NOW)
        assert u["status"] == "exhausted"

    def test_stale_call_is_released(self):
        old = {"attempts": 1, "claimed_at": (NOW - timedelta(hours=3)).isoformat()}
        u = after_call(None, "in-progress", old, 3, 24, NOW)
        assert u["status"] == "pending" and u["last_outcome"] == "stale"


# ─────────────────────────────────────────────────────────────────────────────
# Runner
# ─────────────────────────────────────────────────────────────────────────────

class MemStore:
    def __init__(self, numbers, max_concurrent=2, max_attempts=3):
        self.campaign = {"id": CAMP, "tenant_id": TENANT, "bot_id": BOT, "status": "active",
                         "max_concurrent": max_concurrent, "max_attempts": max_attempts}
        self.contacts = {
            f"k{i}": {"id": f"k{i}", "phone_number": n, "lead_id": None, "status": "pending",
                      "attempts": 0, "next_attempt_at": (NOW - timedelta(minutes=1)).isoformat(),
                      "current_call_id": None, "claimed_at": None}
            for i, n in enumerate(numbers)
        }
        self.calls = {}   # call_id -> (attempt outcome, call status)
        self.steal = set()

    async def list_active_campaigns(self):
        return [self.campaign] if self.campaign["status"] == "active" else []

    async def load_ivr_config(self, t, b):
        return {"retry_interval_hours": 24}

    async def list_contacts(self, cid, status):
        return [dict(c) for c in self.contacts.values() if c["status"] == status]

    async def due_contacts(self, cid, now_iso, limit):
        due = [dict(c) for c in self.contacts.values()
               if c["status"] == "pending" and _ts(c["next_attempt_at"]) <= _ts(now_iso)]
        return sorted(due, key=lambda c: c["next_attempt_at"])[:limit]

    async def claim_contact(self, cid, now_iso):
        if cid in self.steal or self.contacts[cid]["status"] != "pending":
            return False
        self.contacts[cid].update(status="dialing", claimed_at=now_iso)
        return True

    async def update_contact(self, cid, update):
        self.contacts[cid].update(update)

    async def get_call_progress(self, call_id):
        return self.calls.get(call_id, (None, "failed"))

    async def contact_status_counts(self, cid):
        out = {}
        for c in self.contacts.values():
            out[c["status"]] = out.get(c["status"], 0) + 1
        return out

    async def set_campaign_status(self, cid, t, status):
        self.campaign["status"] = status


def make_dialer(store, blocked=None):
    blocked = blocked or {}
    dialed = []

    async def dial(_store, tenant_id, bot_id, number, campaign_id, lead_id):
        assert (tenant_id, bot_id, campaign_id) == (TENANT, BOT, CAMP)
        if number in blocked:
            return DialResult(False, blocked[number])
        call_id = f"call-{len(dialed) + 1}"
        dialed.append(number)
        store.calls[call_id] = (None, "ringing")
        return DialResult(True, "ok", call_id=call_id)

    return dial, dialed


def run(runner, at=NOW):
    runner.clock = lambda: at
    asyncio.run(runner.tick())


def test_respects_concurrency_and_reconciles():
    store = MemStore(["+11", "+12", "+13", "+14"], max_concurrent=2)
    dial, dialed = make_dialer(store)
    runner = CampaignRunner(store, dial=dial)

    run(runner)
    assert dialed == ["+11", "+12"]                      # only 2 live at once

    run(runner)
    assert len(dialed) == 2                              # both still ringing → no new dials

    store.calls["call-1"] = ("completed", "completed")   # answered
    store.calls["call-2"] = ("voicemail", "completed")   # voicemail
    run(runner)
    assert store.contacts["k0"]["status"] == "done"
    assert store.contacts["k1"]["status"] == "pending"  # retry tomorrow, not now
    assert _ts(store.contacts["k1"]["next_attempt_at"]) == NOW + timedelta(hours=24)
    assert dialed == ["+11", "+12", "+13", "+14"]        # freed slots went to the next contacts


def test_permanent_block_and_completion():
    store = MemStore(["+11", "+12"], max_concurrent=5)
    dial, dialed = make_dialer(store, blocked={"+12": "dnc"})
    runner = CampaignRunner(store, dial=dial)

    run(runner)
    assert store.contacts["k1"]["status"] == "blocked" and store.contacts["k1"]["last_outcome"] == "dnc"
    assert store.campaign["status"] == "active"          # +11 is still on a call

    store.calls["call-1"] = ("completed", "completed")
    run(runner)
    assert store.contacts["k0"]["status"] == "done"
    assert store.campaign["status"] == "completed"


def test_retry_then_exhaust():
    store = MemStore(["+11"], max_concurrent=1, max_attempts=2)
    dial, dialed = make_dialer(store)
    runner = CampaignRunner(store, dial=dial)

    run(runner)
    store.calls["call-1"] = ("no_answer", "no-answer")
    run(runner)
    assert store.contacts["k0"]["status"] == "pending" and len(dialed) == 1

    later = NOW + timedelta(hours=25)
    run(runner, at=later)                                # due again → second attempt
    assert len(dialed) == 2
    store.calls["call-2"] = ("busy", "busy")
    run(runner, at=later)
    assert store.contacts["k0"]["status"] == "exhausted"
    assert store.campaign["status"] == "completed"


def test_lost_claim_is_not_dialed():
    store = MemStore(["+11", "+12"])
    store.steal = {"k0"}
    dial, dialed = make_dialer(store)
    run(CampaignRunner(store, dial=dial))
    assert dialed == ["+12"]


def test_paused_campaign_is_ignored():
    store = MemStore(["+11"])
    store.campaign["status"] = "paused"
    dial, dialed = make_dialer(store)
    run(CampaignRunner(store, dial=dial))
    assert dialed == []
