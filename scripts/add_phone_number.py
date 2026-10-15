#!/usr/bin/env python3
"""
Routes a Twilio number to a tenant's bot so the Voice Gateway answers it.

    python scripts/add_phone_number.py --number +14435550123
    python scripts/add_phone_number.py --number +14435550123 --voice-id <elevenlabs_voice_id>

Writes (idempotently):
  1. phone_numbers   — the number → tenant/bot mapping /voice/inbound looks up
  2. ivr_bot_configs — per-bot voice settings (only if --voice-id is given or none exists)

The Twilio side is configured separately in the console — see docs/VOICE_GATEWAY.md.
"""

import os
import sys
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dotenv
dotenv.load_dotenv()

from supabase import create_client


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--number", required=True, help="E.164, e.g. +14435550123")
    parser.add_argument("--tenant-id", default=os.getenv("TEST_TENANT_ID"),
                        help="Defaults to TEST_TENANT_ID from .env")
    parser.add_argument("--bot-id", default=os.getenv("TEST_BOT_ID"),
                        help="Defaults to TEST_BOT_ID from .env")
    parser.add_argument("--voice-id", help="ElevenLabs voice id for this bot")
    parser.add_argument("--no-recording", action="store_true", help="Disable call recording for this bot")
    args = parser.parse_args()

    if not args.number.startswith("+"):
        print("ERROR: --number must be E.164 (start with +country code)")
        return 1
    if not args.tenant_id or not args.bot_id:
        print("ERROR: --tenant-id and --bot-id are required (or set TEST_TENANT_ID / TEST_BOT_ID)")
        return 1

    url, key = os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_KEY")
    if not url or not key:
        print("ERROR: SUPABASE_URL and SUPABASE_SERVICE_KEY must be set in .env")
        return 1
    db = create_client(url, key)

    bot = db.table("bots").select("id, tenant_id, name").eq("id", args.bot_id).execute()
    if not bot.data or str(bot.data[0]["tenant_id"]) != args.tenant_id:
        print(f"ERROR: bot {args.bot_id} not found in tenant {args.tenant_id}")
        return 1

    taken = db.table("phone_numbers").select("tenant_id").eq("phone_number", args.number) \
        .eq("is_active", True).execute()
    if taken.data and str(taken.data[0]["tenant_id"]) != args.tenant_id:
        print(f"ERROR: {args.number} is already active for another tenant")
        return 1

    pn = db.table("phone_numbers").upsert({
        "tenant_id": args.tenant_id,
        "bot_id": args.bot_id,
        "phone_number": args.number,
        "direction": "both",
        "is_active": True,
    }, on_conflict="tenant_id,phone_number").execute()
    phone_number_id = pn.data[0]["id"]

    existing = db.table("ivr_bot_configs").select("id").eq("tenant_id", args.tenant_id) \
        .eq("bot_id", args.bot_id).execute()
    if args.voice_id or args.no_recording or not existing.data:
        cfg = {"tenant_id": args.tenant_id, "bot_id": args.bot_id, "phone_number_id": phone_number_id}
        if args.voice_id:
            cfg["voice_id"] = args.voice_id
        if args.no_recording:
            cfg["call_recording_enabled"] = False
        db.table("ivr_bot_configs").upsert(cfg, on_conflict="tenant_id,bot_id").execute()

    print(f"OK: {args.number} → bot '{bot.data[0]['name']}' ({args.bot_id}), tenant {args.tenant_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
