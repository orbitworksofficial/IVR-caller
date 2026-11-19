"""
Links a live call to the shared long-term memory (implementation plan §06/§12,
Phase 2 — Hammad: "call-specific memory … linked to shared long-term memory").

At call start: who is this number? Prior call summaries for the same tenant +
phone number, and the matching contact / lead profile captured by the chatbot
or earlier calls. The result is a compact block injected into the voice prompt
so a returning caller isn't asked the same discovery questions twice.

At call end: the call row is linked to the lead so the CRM sees chat and call
history together.

Every query is filtered by tenant_id. All failures degrade to "no history" —
memory must never block or delay answering the phone.
"""

import asyncio
import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

MAX_PRIOR_CALLS = 3
LOOKUP_TIMEOUT_S = 1.5  # call start must stay fast; history is best-effort


def phone_variants(number: str) -> List[str]:
    """E.164 plus the digit-only forms a contact form may have stored."""
    if not number:
        return []
    digits = re.sub(r"\D", "", number)
    variants = {number.strip(), digits, f"+{digits}"}
    if len(digits) == 11 and digits.startswith("1"):
        variants.add(digits[1:])  # US national format
    return [v for v in variants if v and v != "+"]


class CallerHistoryLoader:
    def __init__(self, supabase_client: Any):
        self.db = supabase_client

    async def _run(self, fn):
        return await asyncio.to_thread(fn)

    async def load(
        self,
        tenant_id: str,
        phone_number: str,
        exclude_call_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Returns {"prior_calls": [...], "profile": {...}} — both may be empty.
        """
        if not (tenant_id and phone_number):
            return {"prior_calls": [], "profile": {}}
        try:
            return await asyncio.wait_for(
                self._load(tenant_id, phone_number, exclude_call_id), LOOKUP_TIMEOUT_S
            )
        except Exception as e:
            logger.warning(f"Caller history lookup skipped for tenant {tenant_id}: {e}")
            return {"prior_calls": [], "profile": {}}

    async def _load(self, tenant_id: str, phone_number: str, exclude_call_id: Optional[str]) -> Dict[str, Any]:
        variants = phone_variants(phone_number)

        def q_calls():
            # Inbound: caller is from_number. Outbound: caller is to_number.
            return (
                self.db.table("calls")
                .select("id, direction, started_at, outcome, call_summary, from_number, to_number, lead_id")
                .eq("tenant_id", tenant_id)
                .or_(
                    ",".join(
                        [f"from_number.eq.{v}" for v in variants] + [f"to_number.eq.{v}" for v in variants]
                    )
                )
                .order("started_at", desc=True)
                .limit(MAX_PRIOR_CALLS + 1)
                .execute()
            )

        def q_contact():
            return (
                self.db.table("contacts")
                .select("id, name, email, company")
                .eq("tenant_id", tenant_id)
                .in_("phone", variants)
                .limit(1)
                .execute()
            )

        calls_res, contact_res = await asyncio.gather(
            self._run(q_calls), self._run(q_contact), return_exceptions=True
        )

        prior_calls: List[Dict[str, Any]] = []
        lead_id = None
        if not isinstance(calls_res, Exception):
            for row in calls_res.data or []:
                if exclude_call_id and str(row.get("id")) == str(exclude_call_id):
                    continue
                lead_id = lead_id or row.get("lead_id")
                if row.get("call_summary"):
                    prior_calls.append({
                        "date": str(row.get("started_at") or "")[:10],
                        "direction": row.get("direction"),
                        "outcome": row.get("outcome"),
                        "summary": row["call_summary"],
                    })
            prior_calls = prior_calls[:MAX_PRIOR_CALLS]
        else:
            logger.warning(f"Prior-call lookup failed: {calls_res}")

        profile: Dict[str, Any] = {}
        contact_id = None
        if not isinstance(contact_res, Exception) and contact_res.data:
            c = contact_res.data[0]
            contact_id = c.get("id")
            profile.update({k: c[k] for k in ("name", "company", "email") if c.get(k)})

        lead = await self._load_lead(tenant_id, lead_id, contact_id)
        if lead:
            profile["lead_id"] = lead.get("id")
            if lead.get("lead_stage"):
                profile["lead_stage"] = lead["lead_stage"]
            if lead.get("services_of_interest"):
                profile["services_of_interest"] = list(lead["services_of_interest"])
            for k in ("name", "company"):
                if lead.get(k) and not profile.get(k):
                    profile[k] = lead[k]
            if lead.get("requirement"):
                profile["requirement"] = lead["requirement"]

        return {"prior_calls": prior_calls, "profile": profile}

    async def _load_lead(self, tenant_id: str, lead_id: Optional[str], contact_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not (lead_id or contact_id):
            return None

        def q():
            query = (
                self.db.table("leads")
                .select("id, name, company, requirement, lead_stage, services_of_interest")
                .eq("tenant_id", tenant_id)
            )
            query = query.eq("id", lead_id) if lead_id else query.eq("contact_id", contact_id)
            return query.order("created_at", desc=True).limit(1).execute()

        try:
            res = await self._run(q)
            return res.data[0] if res.data else None
        except Exception as e:
            logger.warning(f"Lead lookup failed: {e}")
            return None

    async def link_call_to_lead(self, tenant_id: str, call_id: str, lead_id: Optional[str]) -> None:
        if not (tenant_id and call_id and lead_id):
            return

        def q():
            return (
                self.db.table("calls")
                .update({"lead_id": lead_id})
                .eq("id", call_id)
                .eq("tenant_id", tenant_id)
                .execute()
            )

        try:
            await self._run(q)
        except Exception as e:
            logger.warning(f"Could not link call {call_id} to lead {lead_id}: {e}")


def format_caller_history(history: Dict[str, Any]) -> str:
    """Compact prompt block — kept short because every token costs latency."""
    prior = (history or {}).get("prior_calls") or []
    if not prior:
        return ""
    lines = [
        f"{p.get('date') or 'earlier'} ({p.get('direction') or 'call'}, {p.get('outcome') or 'no outcome'}): {p['summary']}"
        for p in prior
    ]
    return (
        "Returning caller. Previous calls:\n" + "\n".join(lines)
        + "\nUse this history to avoid repeating questions; do not read it back verbatim."
    )
