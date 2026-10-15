#!/usr/bin/env python3
"""
Create and run outbound call campaigns. Stopgap until the Phase 4 admin UI.

    python scripts/voice_campaign.py create --name "Sept web leads" --reason "Follow up on their website enquiry"
    python scripts/voice_campaign.py add    <campaign_id> +14435550123 +14435550124
    python scripts/voice_campaign.py add    <campaign_id> --csv leads.csv     # columns: phone_number[,lead_id]
    python scripts/voice_campaign.py start  <campaign_id>                     # runner picks it up (VOICE_CAMPAIGN_RUNNER=true)
    python scripts/voice_campaign.py pause  <campaign_id>
    python scripts/voice_campaign.py status <campaign_id>

Adding a number to a campaign grants nothing: each dial still needs consent,
no DNC entry and the calling window (see voice_consent.py).
Tenant/bot default to TEST_TENANT_ID / TEST_BOT_ID from .env.
"""

import argparse
import csv
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dotenv
dotenv.load_dotenv()

from supabase import create_client

from apps.voice_gateway.dial_policy import E164


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("action", choices=["create", "add", "start", "pause", "status"])
    p.add_argument("campaign_id", nargs="?")
    p.add_argument("numbers", nargs="*")
    p.add_argument("--tenant-id", default=os.getenv("TEST_TENANT_ID"))
    p.add_argument("--bot-id", default=os.getenv("TEST_BOT_ID"))
    p.add_argument("--name")
    p.add_argument("--reason", default="", help="Why we're calling; injected into the outbound prompt")
    p.add_argument("--type", default="follow_up", help="follow_up | qualification | appointment_reminder")
    p.add_argument("--max-attempts", type=int, default=3)
    p.add_argument("--max-concurrent", type=int, default=2)
    p.add_argument("--csv")
    args = p.parse_args()

    url, key = os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_KEY")
    if not url or not key or not args.tenant_id:
        print("ERROR: SUPABASE_URL, SUPABASE_SERVICE_KEY and a tenant id (TEST_TENANT_ID) are required")
        return 1
    db = create_client(url, key)
    t = args.tenant_id

    if args.action == "create":
        if not args.name or not args.bot_id:
            print("ERROR: --name and a bot id (--bot-id or TEST_BOT_ID) are required")
            return 1
        row = db.table("call_campaigns").insert({
            "tenant_id": t, "bot_id": args.bot_id, "name": args.name, "status": "draft",
            "campaign_type": args.type, "call_reason": args.reason or None,
            "max_attempts": args.max_attempts, "max_concurrent": args.max_concurrent,
        }).execute().data[0]
        print(f"OK: created draft campaign {row['id']} — add contacts, then `start`")
        return 0

    if not args.campaign_id:
        print("ERROR: campaign_id is required")
        return 1
    camp = db.table("call_campaigns").select("*").eq("id", args.campaign_id).eq("tenant_id", t).execute()
    if not camp.data:
        print(f"ERROR: campaign {args.campaign_id} not found in tenant {t}")
        return 1
    cid = args.campaign_id

    if args.action == "add":
        rows = [(n, None) for n in args.numbers]
        if args.csv:
            with open(args.csv, newline="", encoding="utf-8") as f:
                for r in csv.DictReader(f):
                    rows.append((r["phone_number"].strip(), (r.get("lead_id") or "").strip() or None))
        bad = [n for n, _ in rows if not E164.match(n)]
        if bad:
            print(f"ERROR: not E.164: {', '.join(bad[:10])}")
            return 1
        if not rows:
            print("ERROR: no numbers given")
            return 1
        db.table("campaign_contacts").upsert(
            [{"tenant_id": t, "campaign_id": cid, "phone_number": n, "lead_id": lead} for n, lead in rows],
            on_conflict="campaign_id,phone_number", ignore_duplicates=True,
        ).execute()
        print(f"OK: {len(rows)} contact(s) added (duplicates ignored)")
    elif args.action in ("start", "pause"):
        status = "active" if args.action == "start" else "paused"
        db.table("call_campaigns").update({"status": status}).eq("id", cid).eq("tenant_id", t).execute()
        print(f"OK: campaign {cid} is {status}")
        if status == "active" and os.getenv("VOICE_CAMPAIGN_RUNNER", "false").lower() != "true":
            print("NOTE: VOICE_CAMPAIGN_RUNNER is not true here — make sure it is on the gateway server.")
    else:
        contacts = db.table("campaign_contacts").select("status, last_outcome").eq("campaign_id", cid).execute().data
        c = camp.data[0]
        print(f"{c['name']} [{c['status']}]  max_attempts={c.get('max_attempts')} max_concurrent={c.get('max_concurrent')}")
        print("  by status: ", dict(Counter(r["status"] for r in contacts)) or "no contacts")
        print("  by outcome:", dict(Counter(r["last_outcome"] for r in contacts if r["last_outcome"])) or "-")
    return 0


if __name__ == "__main__":
    sys.exit(main())
