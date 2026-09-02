#!/usr/bin/env python3
"""
Creates a dashboard login and links it to a tenant.

    python scripts/create_admin.py --email you@company.com --password 'secret123'

Two steps, both requiring the service-role key:
  1. Create the user in Supabase Auth (admin API)
  2. Insert the tenant_admins row that grants access to a tenant

Without step 2 the user can sign in but every admin endpoint returns 403 —
authentication and authorization are deliberately separate.
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
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", required=True, help="Minimum 6 characters.")
    parser.add_argument("--tenant-id", default=os.getenv("TEST_TENANT_ID"),
                        help="Defaults to TEST_TENANT_ID from .env")
    parser.add_argument("--role", default="admin")
    args = parser.parse_args()

    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_KEY")
    if not url or not key:
        print("ERROR: SUPABASE_URL and SUPABASE_SERVICE_KEY must be set in .env")
        return 1
    if not args.tenant_id:
        print("ERROR: no tenant id. Pass --tenant-id or set TEST_TENANT_ID in .env")
        return 1
    if len(args.password) < 6:
        print("ERROR: password must be at least 6 characters.")
        return 1

    supabase = create_client(url, key)

    # Confirm the tenant exists before creating a user that points at nothing.
    tenant = supabase.table("tenants").select("id, name").eq(
        "id", args.tenant_id
    ).execute()
    if not tenant.data:
        print(f"ERROR: tenant '{args.tenant_id}' not found.")
        return 1
    tenant_name = tenant.data[0]["name"]

    # ── 1. Create (or find) the auth user ────────────────────────────────────
    user_id = None
    try:
        created = supabase.auth.admin.create_user({
            "email": args.email,
            "password": args.password,
            # Skip the confirmation email — this is an internal admin account.
            "email_confirm": True,
        })
        user_id = created.user.id
        print(f"[1/2] User created        {args.email}")
    except Exception as e:
        if "already" in str(e).lower() or "registered" in str(e).lower():
            # Find the existing user so the script stays re-runnable.
            page = supabase.auth.admin.list_users()
            users = page if isinstance(page, list) else getattr(page, "users", [])
            for u in users:
                if (getattr(u, "email", "") or "").lower() == args.email.lower():
                    user_id = u.id
                    break
            if not user_id:
                print(f"ERROR: user exists but could not be looked up: {e}")
                return 1
            print(f"[1/2] User already exists {args.email}")
        else:
            print(f"ERROR creating user: {e}")
            return 1

    # ── 2. Link the user to the tenant ───────────────────────────────────────
    existing = supabase.table("tenant_admins").select("id").eq(
        "user_id", user_id
    ).eq("tenant_id", args.tenant_id).execute()

    if existing.data:
        print(f"[2/2] Already linked to   {tenant_name}")
    else:
        supabase.table("tenant_admins").insert({
            "user_id": user_id,
            "tenant_id": args.tenant_id,
            "role": args.role,
        }).execute()
        print(f"[2/2] Linked to tenant    {tenant_name}")

    print()
    print("=" * 58)
    print("  Dashboard login ready")
    print("=" * 58)
    print(f"  URL:      http://localhost:3000/login")
    print(f"  Email:    {args.email}")
    print(f"  Password: (the one you provided)")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
