#!/usr/bin/env python3
"""
Manage outbound-calling consent and the do-not-call list, and run dial checks.
Stopgap until the Phase 4 admin UI.

    python scripts/voice_consent.py grant  +14435550123 --source web_form --evidence "form #812"
    python scripts/voice_consent.py revoke +14435550123
    python scripts/voice_consent.py dnc    +14435550123 --reason "asked by email"
    python scripts/voice_consent.py undnc  +14435550123
    python scripts/voice_consent.py check  +14435550123     # dry run: would a dial be allowed now?
    python scripts/voice_consent.py dial   +14435550123     # place a real outbound call

Tenant/bot default to TEST_TENANT_ID / TEST_BOT_ID from .env.
"""

import argparse
import asyncio
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dotenv
dotenv.load_dotenv()

from supabase import create_client

from apps.voice_gateway.call_store import CallStore
from apps.voice_gateway.dial_policy import E164, check_dial
from apps.voice_gateway.outbound import place_outbound_call


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("action", choices=["grant", "revoke", "dnc", "undnc", "check", "dial"])
    p.add_argument("number", help="E.164, e.g. +14435550123")
    p.add_argument("--tenant-id", default=os.getenv("TEST_TENANT_ID"))
    p.add_argument("--bot-id", default=os.getenv("TEST_BOT_ID"))
    p.add_argument("--source", default="crm_import", help="web_form | verbal_on_call | crm_import | chatbot")
    p.add_argument("--evidence", default="")
    p.add_argument("--reason", default="")
    p.add_argument("--campaign-id")
    args = p.parse_args()

    if not E164.match(args.number):
        print("ERROR: number must be E.164 (e.g. +14435550123)")
        return 1
    if not args.tenant_id:
        print("ERROR: --tenant-id required (or TEST_TENANT_ID in .env)")
        return 1
    url, key = os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_KEY")
    if not url or not key:
        print("ERROR: SUPABASE_URL and SUPABASE_SERVICE_KEY must be set in .env")
        return 1
    db = create_client(url, key)
    t, n = args.tenant_id, args.number

    if args.action == "grant":
        # Upsert clears revoked_at, re-activating a previously revoked consent.
        db.table("consent_records").upsert({
            "tenant_id": t, "phone_number": n, "consent_type": "call",
            "source": args.source, "evidence_text": args.evidence or None,
            "granted_at": datetime.now(timezone.utc).isoformat(), "revoked_at": None,
        }, on_conflict="tenant_id,phone_number,consent_type").execute()
        print(f"OK: call consent granted for {n}")
    elif args.action == "revoke":
        db.table("consent_records").update({"revoked_at": datetime.now(timezone.utc).isoformat()}) \
            .eq("tenant_id", t).eq("phone_number", n).eq("consent_type", "call").execute()
        print(f"OK: call consent revoked for {n}")
    elif args.action == "dnc":
        db.table("dnc_list").upsert({
            "tenant_id": t, "phone_number": n, "source": "internal", "reason": args.reason or None,
        }, on_conflict="tenant_id,phone_number").execute()
        print(f"OK: {n} added to DNC")
    elif args.action == "undnc":
        db.table("dnc_list").delete().eq("tenant_id", t).eq("phone_number", n).execute()
        print(f"OK: {n} removed from DNC")
    else:
        if not args.bot_id:
            print("ERROR: --bot-id required (or TEST_BOT_ID in .env)")
            return 1
        store = CallStore(db)
        if args.action == "check":
            campaign = asyncio.run(store.get_campaign(t, args.campaign_id)) if args.campaign_id else None
            d = asyncio.run(check_dial(store, t, args.bot_id, n, campaign))
            print(f"{'ALLOWED' if d.allowed else 'BLOCKED'}: {d.reason} {d.detail}")
            return 0 if d.allowed else 2
        r = asyncio.run(place_outbound_call(store, t, args.bot_id, n, args.campaign_id))
        print(f"{'DIALED' if r.dialed else 'NOT DIALED'}: {r.reason} {r.detail} call_id={r.call_id} sid={r.call_sid}")
        return 0 if r.dialed else 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
