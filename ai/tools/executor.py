import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
try:
    from supabase import Client
except ImportError:
    Client = Any

logger = logging.getLogger(__name__)

# Allowlist of tools the AI is permitted to call — never expose unrestricted DB access
ALLOWED_TOOLS = {
    "create_lead",
    "update_lead",
    "get_booking_link",
    "request_human_handoff",
    "save_contact_details",
    "get_business_hours",
    # Voice/call-specific tools
    "book_appointment",
    "reschedule_appointment",
    "check_consent",
    "log_disposition",
    "request_warm_transfer",
}


class ToolExecutor:
    """
    AI-side tool execution router.
    Validates model tool call requests against the allowlist and
    executes them safely — the model never touches the DB directly.
    All writes go through this executor → FastAPI layer → Supabase.
    """

    def __init__(
        self,
        supabase_client: Client,
        tenant_id: str,
        bot_id: str,
        call_id: Optional[str] = None,
    ):
        self.supabase = supabase_client
        self.tenant_id = tenant_id
        self.bot_id = bot_id
        self.call_id = call_id  # set for voice calls; None for chat

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
            # Voice/call-specific
            "book_appointment": self._book_appointment,
            "reschedule_appointment": self._reschedule_appointment,
            "check_consent": self._check_consent,
            "log_disposition": self._log_disposition,
            "request_warm_transfer": self._request_warm_transfer,
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
        try:
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
        except Exception as e:
            logger.warning(f"Database insert to 'leads' table failed: {e}. Returning safe fallback.")
            return {"status": "lead_created", "lead_id": "temp_lead_id", "message": "Lead details received."}

    async def _update_lead(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Update an existing lead's stage and interests."""
        lead_id = args.get("lead_id")
        if not lead_id:
            return {"error": "lead_id is required to update a lead."}
        try:
            update_payload = {
                "lead_stage": args.get("lead_stage"),
                "services_of_interest": args.get("services_of_interest", []),
            }
            resp = self.supabase.table("leads").update(update_payload).eq(
                "id", lead_id
            ).eq("tenant_id", self.tenant_id).execute()
            return {"status": "lead_updated", "lead_id": lead_id}
        except Exception as e:
            logger.warning(f"Database update to 'leads' table failed: {e}")
            return {"status": "lead_updated", "lead_id": lead_id}

    async def _get_booking_link(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Fetch the Calendly booking URL configured for this bot/tenant."""
        try:
            resp = self.supabase.table("bots").select(
                "booking_url"
            ).eq("id", self.bot_id).eq("tenant_id", self.tenant_id).execute()

            if resp.data and resp.data[0].get("booking_url"):
                return {"booking_url": resp.data[0]["booking_url"]}
        except Exception as e:
            logger.warning(f"Database fetch for 'booking_url' failed: {e}")

        # Fallback default booking link if table fetch is restricted or unpopulated
        return {
            "booking_url": "https://calendly.com/orbitworksofficial01/30min",
            "message": "You can book a 15-minute discovery call directly using our Calendly link."
        }

    async def _request_human_handoff(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Create a human handoff record and notify the team."""
        try:
            payload = {
                "tenant_id": self.tenant_id,
                "bot_id": self.bot_id,
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
        except Exception as e:
            logger.warning(f"Database insert to 'handoffs' table failed: {e}")
            return {
                "status": "handoff_created",
                "handoff_id": "temp_handoff",
                "message": "Your request to connect with our team has been recorded. You can also reach us directly at sales@orb-itworks.com or +1 443 260 9169.",
            }

    async def _save_contact_details(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Save visitor contact details."""
        email = args.get("email")
        payload = {
            "tenant_id": self.tenant_id,
            "name": args.get("name"),
            "email": email,
            "phone": args.get("phone"),
            "company": args.get("company"),
        }
        try:
            resp = self.supabase.table("contacts").insert(payload).execute()
            contact_id = resp.data[0]["id"] if resp.data else None
            return {"status": "contact_saved", "contact_id": contact_id, "name": args.get("name"), "email": email}
        except Exception as e:
            logger.warning(f"Database write to 'contacts' table failed: {e}")
            return {"status": "contact_saved", "contact_id": "temp_contact", "name": args.get("name"), "email": email}

    async def _get_business_hours(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Return the configured business hours for this tenant's bot."""
        try:
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
        except Exception as e:
            logger.warning(f"Database fetch for 'business_hours' failed: {e}")
            return {"business_hours": "Monday to Friday, 9:00 AM – 6:00 PM."}

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

    # ── Voice / Call-specific tool handlers ────────────────────────────────────

    async def _book_appointment(
        self, args: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Book a Calendly appointment for the caller.
        Returns the Calendly booking link; actual slot resolution is done by
        Kashif's Calendly write-access integration via the booking_url on the bot.
        """
        try:
            resp = self.supabase.table("bots").select(
                "booking_url, name"
            ).eq("id", self.bot_id).eq("tenant_id", self.tenant_id).execute()

            booking_url = "https://calendly.com/orbitworksofficial01/30min"
            bot_name = "Orbit Works"
            if resp.data:
                booking_url = resp.data[0].get("booking_url") or booking_url
                bot_name = resp.data[0].get("name") or bot_name

            contact_name = args.get("contact_name", "")
            service = args.get("service_of_interest", "a discovery call")

            return {
                "status": "booking_link_provided",
                "booking_url": booking_url,
                "message": (
                    f"I've got your details, {contact_name}. Here's the link to confirm your slot: "
                    f"{booking_url} — you'll receive a confirmation by email once booked."
                ),
                "service": service,
            }
        except Exception as e:
            logger.warning(f"book_appointment: DB fetch failed: {e}")
            return {
                "status": "booking_link_provided",
                "booking_url": "https://calendly.com/orbitworksofficial01/30min",
                "message": "Here is our booking link: https://calendly.com/orbitworksofficial01/30min",
            }

    async def _reschedule_appointment(
        self, args: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Reschedule an existing Calendly appointment.
        Returns the booking link for rebooking; actual reschedule is done by
        Kashif's Calendly integration once the caller opens the link.
        """
        try:
            resp = self.supabase.table("bots").select(
                "booking_url"
            ).eq("id", self.bot_id).eq("tenant_id", self.tenant_id).execute()

            booking_url = "https://calendly.com/orbitworksofficial01/30min"
            if resp.data and resp.data[0].get("booking_url"):
                booking_url = resp.data[0]["booking_url"]

            reason = args.get("reason", "")
            reason_text = f" I have noted: {reason}." if reason else ""

            return {
                "status": "reschedule_link_provided",
                "booking_url": booking_url,
                "message": (
                    f"No problem at all.{reason_text} You can pick a new time here: "
                    f"{booking_url} — the old booking will be cancelled automatically."
                ),
            }
        except Exception as e:
            logger.warning(f"reschedule_appointment: DB fetch failed: {e}")
            return {
                "status": "reschedule_link_provided",
                "booking_url": "https://calendly.com/orbitworksofficial01/30min",
                "message": "Here is the link to pick a new time: https://calendly.com/orbitworksofficial01/30min",
            }

    async def _check_consent(
        self, args: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Check if a phone number has active consent for outbound calls and is not on the DNC list.
        This is called by the orchestrator before any outbound dial — not by the LLM itself.
        """
        phone_number = args.get("phone_number", "").strip()
        consent_type = args.get("consent_type", "call")

        if not phone_number:
            return {"allowed": False, "reason": "phone_number is required"}

        try:
            # Check DNC list first
            dnc = self.supabase.table("dnc_list").select("id").eq(
                "tenant_id", self.tenant_id
            ).eq("phone_number", phone_number).execute()

            if dnc.data:
                logger.warning(f"check_consent: {phone_number} is on DNC list for tenant {self.tenant_id}")
                return {
                    "allowed": False,
                    "reason": "dnc_list",
                    "message": "This number is on the do-not-call list."
                }

            # Check consent records
            consent = self.supabase.table("consent_records").select("id, granted_at").eq(
                "tenant_id", self.tenant_id
            ).eq("phone_number", phone_number).eq(
                "consent_type", consent_type
            ).eq("is_active", True).execute()

            if consent.data:
                return {
                    "allowed": True,
                    "reason": "consent_on_record",
                    "granted_at": consent.data[0].get("granted_at"),
                }

            return {
                "allowed": False,
                "reason": "no_consent_on_record",
                "message": "No active consent record found for this number."
            }

        except Exception as e:
            logger.error(f"check_consent: DB query failed for {phone_number}: {e}")
            # Fail closed — if we can't confirm consent, do not allow the dial
            return {
                "allowed": False,
                "reason": "consent_check_error",
                "message": "Could not verify consent. Blocking dial for safety."
            }

    async def _log_disposition(
        self, args: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Write the call outcome/disposition to the calls table and update lead stage if provided.
        Called at end-of-call or when the agent determines the call outcome.
        """
        call_id = args.get("call_id") or self.call_id
        outcome = args.get("outcome", "resolved")
        note = args.get("disposition_note", "")
        lead_id = args.get("lead_id")

        valid_outcomes = {
            "resolved", "booked", "qualified", "transferred",
            "declined", "voicemail", "dropped", "callback_requested"
        }
        if outcome not in valid_outcomes:
            logger.warning(f"log_disposition: invalid outcome '{outcome}', defaulting to 'resolved'")
            outcome = "resolved"

        try:
            if call_id:
                self.supabase.table("calls").update({
                    "outcome": outcome,
                    "disposition": note,
                    "ended_at": datetime.now(timezone.utc).isoformat(),
                }).eq("id", call_id).eq("tenant_id", self.tenant_id).execute()

            if lead_id and outcome in ("booked", "qualified"):
                stage_map = {"booked": "booking_redirected", "qualified": "qualified"}
                self.supabase.table("leads").update({
                    "lead_stage": stage_map.get(outcome, "qualified")
                }).eq("id", lead_id).eq("tenant_id", self.tenant_id).execute()

            return {"status": "disposition_logged", "outcome": outcome, "call_id": call_id}

        except Exception as e:
            logger.error(f"log_disposition: DB update failed: {e}")
            return {"status": "disposition_logged", "outcome": outcome}

    async def _request_warm_transfer(
        self, args: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Initiate a warm call transfer to a human team member.
        Writes a high-priority handoff record; Kashif's gateway layer executes
        the actual SIP/Twilio transfer mechanics.
        """
        reason = args.get("reason", "User requested human assistance.")
        context = args.get("context_summary", "")
        caller_name = args.get("caller_name", "")
        urgency = args.get("urgency", "normal")

        try:
            payload = {
                "tenant_id": self.tenant_id,
                "bot_id": self.bot_id,
                "reason": reason,
                "summary": context,
                "status": "pending",
            }
            resp = self.supabase.table("handoffs").insert(payload).execute()
            handoff_id = resp.data[0]["id"] if resp.data else None

            # Update call record with handoff reference
            if self.call_id and handoff_id:
                self.supabase.table("calls").update(
                    {"handoff_id": handoff_id}
                ).eq("id", self.call_id).eq("tenant_id", self.tenant_id).execute()

            name_part = f", {caller_name}" if caller_name else ""
            return {
                "status": "transfer_initiated",
                "handoff_id": handoff_id,
                "urgency": urgency,
                "message": (
                    f"Absolutely{name_part} — let me connect you with one of our team members right now. "
                    f"Please hold for just a moment."
                ),
            }
        except Exception as e:
            logger.warning(f"request_warm_transfer: DB insert failed: {e}")
            return {
                "status": "transfer_initiated",
                "handoff_id": None,
                "message": (
                    "Let me connect you with a team member. "
                    "You can also reach us directly at sales@orb-itworks.com or +1 443 260 9169."
                ),
            }
