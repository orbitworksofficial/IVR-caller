from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any

class CreateLeadSchema(BaseModel):
    """Tool schema for creating a new sales lead."""
    name: str = Field(..., description="The contact's full name.")
    email: str = Field(..., description="The contact's professional email address.")
    company: Optional[str] = Field(None, description="The company name of the prospect.")
    requirement: Optional[str] = Field(None, description="Detailed requirements or pain points discussed.")

class UpdateLeadSchema(BaseModel):
    """Tool schema for updating an existing sales lead's stage and interests."""
    lead_id: str = Field(..., description="The UUID of the lead to update.")
    lead_stage: str = Field(..., description="Target lead stage (e.g. 'discovered', 'qualified', 'booking_redirected', 'warm_handoff').")
    services_of_interest: List[str] = Field(default=[], description="List of services or products the prospect is interested in.")

class GetBookingLinkSchema(BaseModel):
    """Tool schema for fetching the Calendly booking URL for the bot tenant."""
    pass

class RequestHumanHandoffSchema(BaseModel):
    """Tool schema for requesting escalation to a human support agent."""
    reason: str = Field(..., description="The primary reason for handoff (e.g., 'technical question unavailable in KB', 'pricing negotiation').")
    summary: str = Field(..., description="Brief summary of the conversation so far to catch up the human agent.")

class SaveContactDetailsSchema(BaseModel):
    """Tool schema for extracting and saving contact details."""
    name: Optional[str] = Field(None, description="The name of the user.")
    email: Optional[str] = Field(None, description="The email address.")
    phone: Optional[str] = Field(None, description="The phone number.")
    company: Optional[str] = Field(None, description="The company name.")

class GetBusinessHoursSchema(BaseModel):
    """Tool schema for retrieving business hours of the tenant."""
    pass


# ── Voice / Call-specific tool schemas ───────────────────────────────────────

class BookAppointmentSchema(BaseModel):
    """Tool schema for booking a Calendly appointment live on a call."""
    contact_name: str = Field(..., description="Full name of the caller booking the appointment.")
    contact_phone: str = Field(..., description="Caller's phone number in E.164 format.")
    contact_email: Optional[str] = Field(None, description="Caller's email if provided.")
    service_of_interest: Optional[str] = Field(None, description="Service or product the appointment is for.")
    preferred_time: Optional[str] = Field(
        None,
        description="Caller's stated preference for time (e.g. 'tomorrow afternoon', 'next Monday morning'). "
                    "Send the Calendly link — Kashif's integration handles actual slot resolution."
    )


class RescheduleAppointmentSchema(BaseModel):
    """Tool schema for rescheduling an existing Calendly booking."""
    booking_reference: Optional[str] = Field(
        None,
        description="Calendly booking ID or event UUID if known."
    )
    contact_phone: str = Field(..., description="Caller's phone number to identify the booking.")
    new_preferred_time: Optional[str] = Field(
        None,
        description="Caller's stated new preferred time. Kashif's integration handles actual slot resolution."
    )
    reason: Optional[str] = Field(None, description="Reason for reschedule if the caller mentioned one.")


class CheckConsentSchema(BaseModel):
    """Tool schema for checking outbound call consent and DNC status before dialling."""
    phone_number: str = Field(..., description="E.164 phone number to check consent for.")
    consent_type: str = Field(
        "call",
        description="Type of consent to verify: 'call', 'sms', or 'email'."
    )


class LogDispositionSchema(BaseModel):
    """Tool schema for writing the call outcome/disposition to the CRM at call end."""
    call_id: str = Field(..., description="UUID of the current call record.")
    outcome: str = Field(
        ...,
        description=(
            "Call outcome. One of: 'resolved', 'booked', 'qualified', "
            "'transferred', 'declined', 'voicemail', 'dropped', 'callback_requested'."
        )
    )
    disposition_note: Optional[str] = Field(
        None,
        description="Short free-text CRM note about what happened on the call."
    )
    lead_id: Optional[str] = Field(None, description="UUID of the lead if one was created or updated.")


class RequestWarmTransferSchema(BaseModel):
    """Tool schema for initiating a live warm transfer to a human team member during a call."""
    reason: str = Field(..., description="Why a human team member is needed right now.")
    context_summary: str = Field(
        ...,
        description="Compact summary of the call so far — the human agent will read this immediately on transfer."
    )
    caller_name: Optional[str] = Field(None, description="Caller's name if known.")
    caller_phone: Optional[str] = Field(None, description="Caller's phone number.")
    urgency: str = Field(
        "normal",
        description="Transfer urgency: 'normal' | 'high' (caller is frustrated or upset)."
    )


# Helper function to generate OpenAI Tool Definition format
def get_tools_definition(voice_mode: bool = False) -> List[Dict[str, Any]]:
    tools = [
        {
            "type": "function",
            "function": {
                "name": "create_lead",
                "description": "Create a new sales lead in the database with the prospect's details.",
                "parameters": CreateLeadSchema.model_json_schema()
            }
        },
        {
            "type": "function",
            "function": {
                "name": "update_lead",
                "description": "Update an existing lead's status stage or specific interests.",
                "parameters": UpdateLeadSchema.model_json_schema()
            }
        },
        {
            "type": "function",
            "function": {
                "name": "get_booking_link",
                "description": "Fetch the Calendly or meeting booking link for this tenant bot.",
                "parameters": GetBookingLinkSchema.model_json_schema()
            }
        },
        {
            "type": "function",
            "function": {
                "name": "request_human_handoff",
                "description": "Trigger a warm handoff to a live customer agent or human specialist.",
                "parameters": RequestHumanHandoffSchema.model_json_schema()
            }
        },
        {
            "type": "function",
            "function": {
                "name": "save_contact_details",
                "description": "Store contact details such as name, email, phone number, and company.",
                "parameters": SaveContactDetailsSchema.model_json_schema()
            }
        },
        {
            "type": "function",
            "function": {
                "name": "get_business_hours",
                "description": "Get the official business operating hours for this tenant business.",
                "parameters": GetBusinessHoursSchema.model_json_schema()
            }
        }
    ]

    # Voice/call-specific tools — only available during phone calls
    if voice_mode:
        tools += [
            {
                "type": "function",
                "function": {
                    "name": "book_appointment",
                    "description": (
                        "Book a Calendly appointment for the caller. Call this when the caller agrees to "
                        "schedule a meeting or discovery call. Returns a booking confirmation link."
                    ),
                    "parameters": BookAppointmentSchema.model_json_schema()
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "reschedule_appointment",
                    "description": (
                        "Reschedule an existing appointment. Call this when the caller says they need to "
                        "change the time of a previously booked meeting."
                    ),
                    "parameters": RescheduleAppointmentSchema.model_json_schema()
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "check_consent",
                    "description": (
                        "Check if a phone number has valid outbound call consent and is not on the DNC list. "
                        "MUST be called before any outbound dial attempt."
                    ),
                    "parameters": CheckConsentSchema.model_json_schema()
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "log_disposition",
                    "description": (
                        "Log the call outcome and disposition note to the CRM at the end of a call. "
                        "Call this when the call is about to end or after a handoff."
                    ),
                    "parameters": LogDispositionSchema.model_json_schema()
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "request_warm_transfer",
                    "description": (
                        "Initiate a live warm transfer to a human team member. Call this when the caller "
                        "explicitly asks for a human, or when the AI cannot resolve the caller's issue. "
                        "Different from request_human_handoff — this is a real-time live call transfer."
                    ),
                    "parameters": RequestWarmTransferSchema.model_json_schema()
                }
            },
        ]

    return tools
