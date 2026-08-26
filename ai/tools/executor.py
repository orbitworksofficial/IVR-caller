import json
import logging
from typing import Any, Dict, List, Optional
from supabase import Client

logger = logging.getLogger(__name__)

# Allowlist of tools the AI is permitted to call — never expose unrestricted DB access
ALLOWED_TOOLS = {
    "create_lead",
    "update_lead",
    "get_booking_link",
    "request_human_handoff",
    "save_contact_details",
    "get_business_hours",
}


class ToolExecutor:
    """
    AI-side tool execution router.
    Validates model tool call requests against the allowlist and
    executes them safely — the model never touches the DB directly.
    All writes go through this executor → FastAPI layer → Supabase.
    """

    def __init__(self, supabase_client: Client, tenant_id: str, bot_id: str):
        self.supabase = supabase_client
        self.tenant_id = tenant_id
        self.bot_id = bot_id

    def is_allowed(self, tool_name: str) -> bool:
        """Check tool is in the allowlist before executing."""
        return tool_name in ALLOWED_TOOLS

    async def execute(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """
        Dispatches a validated tool call to the appropriate handler.
        Returns a structured result dict that is injected back into
        the conversation as a 'tool' role message.
        """
        if not self.is_allowed(tool_name):
            logger.warning(f"Blocked disallowed tool call: '{tool_name}'")
            return {"error": f"Tool '{tool_name}' is not permitted."}

        handlers = {
            "create_lead": self._create_lead,
            "update_lead": self._update_lead,
            "get_booking_link": self._get_booking_link,
            "request_human_handoff": self._request_human_handoff,
            "save_contact_details": self._save_contact_details,
            "get_business_hours": self._get_business_hours,
        }

        handler = handlers[tool_name]
        try:
            result = await handler(arguments)
            logger.info(f"Tool '{tool_name}' executed. Result: {result}")
            return result
        except Exception as e:
            logger.error(f"Tool '{tool_name}' failed: {e}")
            return {"error": str(e)}

    async def _create_lead(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new lead record in the database."""
        payload = {
            "tenant_id": self.tenant_id,
            "name": args.get("name"),
            "email": args.get("email"),
            "company": args.get("company"),
            "requirement": args.get("requirement"),
            "lead_stage": "discovered",
            "source": "chatbot",
        }
        resp = self.supabase.table("leads").insert(payload).execute()
        lead_id = resp.data[0]["id"] if resp.data else None
        return {"status": "lead_created", "lead_id": lead_id}

    async def _update_lead(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Update an existing lead's stage and interests."""
        lead_id = args.get("lead_id")
        if not lead_id:
            return {"error": "lead_id is required to update a lead."}
        update_payload = {
            "lead_stage": args.get("lead_stage"),
            "services_of_interest": args.get("services_of_interest", []),
        }
        resp = self.supabase.table("leads").update(update_payload).eq(
            "id", lead_id
        ).eq("tenant_id", self.tenant_id).execute()
        return {"status": "lead_updated", "lead_id": lead_id}

    async def _get_booking_link(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Fetch the Calendly booking URL configured for this bot/tenant."""
        resp = self.supabase.table("bots").select(
            "booking_url"
        ).eq("id", self.bot_id).eq("tenant_id", self.tenant_id).execute()

        if not resp.data or not resp.data[0].get("booking_url"):
            return {"booking_url": None, "message": "No booking link configured for this bot."}
        return {"booking_url": resp.data[0]["booking_url"]}

    async def _request_human_handoff(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Create a human handoff record and notify the team."""
        payload = {
            "tenant_id": self.tenant_id,
            "reason": args.get("reason", "User requested human assistance."),
            "summary": args.get("summary", ""),
            "status": "pending",
        }
        resp = self.supabase.table("handoffs").insert(payload).execute()
        handoff_id = resp.data[0]["id"] if resp.data else None
        return {
            "status": "handoff_created",
            "handoff_id": handoff_id,
            "message": "A human agent has been notified and will be in touch shortly.",
        }

    async def _save_contact_details(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Save or upsert visitor contact details."""
        email = args.get("email")
        payload = {
            "tenant_id": self.tenant_id,
            "name": args.get("name"),
            "email": email,
            "phone": args.get("phone"),
            "company": args.get("company"),
            "source": "chatbot",
        }
        # Use upsert on email to avoid duplicates
        if email:
            resp = self.supabase.table("contacts").upsert(
                payload, on_conflict="tenant_id,email"
            ).execute()
        else:
            resp = self.supabase.table("contacts").insert(payload).execute()

        contact_id = resp.data[0]["id"] if resp.data else None
        return {"status": "contact_saved", "contact_id": contact_id}

    async def _get_business_hours(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Return the configured business hours for this tenant's bot."""
        resp = self.supabase.table("bots").select(
            "handoff_settings"
        ).eq("id", self.bot_id).eq("tenant_id", self.tenant_id).execute()

        settings = {}
        if resp.data and resp.data[0].get("handoff_settings"):
            settings = resp.data[0]["handoff_settings"]

        hours = settings.get(
            "business_hours",
            "Monday to Friday, 9:00 AM – 6:00 PM Pakistan Standard Time (PKT)."
        )
        return {"business_hours": hours}

    @staticmethod
    def build_tool_result_message(tool_call_id: str, result: Dict[str, Any]) -> Dict[str, Any]:
        """
        Wraps a tool result into the OpenAI 'tool' role message format
        so it can be injected back into the conversation history.
        """
        return {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": json.dumps(result),
        }
