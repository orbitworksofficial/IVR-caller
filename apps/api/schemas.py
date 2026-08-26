from pydantic import BaseModel, Field
from typing import Optional, List

class ChatRequest(BaseModel):
    """Incoming chat request from widget or API consumer."""
    bot_id: str = Field(..., description="UUID of the bot configuration to use.")
    tenant_id: str = Field(..., description="UUID of the tenant this bot belongs to.")
    session_id: str = Field(..., description="Unique session identifier for this conversation.")
    message: str = Field(..., description="The user's chat message.")
    conversation_id: Optional[str] = Field(None, description="Existing conversation UUID to continue (if any).")

class ChatResponse(BaseModel):
    """Outgoing chat response returned to the widget."""
    response: str = Field(..., description="The AI-generated, grounded response.")
    conversation_id: str = Field(..., description="The conversation UUID for this session.")
    grounded: bool = Field(..., description="Whether the response passed grounding checks.")
    model: str = Field(..., description="The model used to generate the response.")

class WidgetConfigResponse(BaseModel):
    """Bot configuration returned to the frontend widget on initialization."""
    bot_id: str
    bot_name: str
    welcome_message: Optional[str]
    booking_url: Optional[str]
    support_mode_enabled: bool
    sales_mode_enabled: bool

class HealthResponse(BaseModel):
    """Health check response."""
    status: str
    version: str
    service: str
