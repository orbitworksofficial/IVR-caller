from pydantic import BaseModel, Field
from typing import List, Optional

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

# Helper function to generate OpenAI Tool Definition format
def get_tools_definition() -> List[dict]:
    return [
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
