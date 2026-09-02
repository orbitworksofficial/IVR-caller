from pydantic import BaseModel, Field, EmailStr
from typing import Optional, List, Dict, Any
from datetime import datetime

# ── Chat (widget-facing) ──────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    """
    Incoming chat request from the widget.

    Note: there is deliberately NO tenant_id field. The tenant is derived
    server-side from bot_id (see apps/api/security.resolve_bot). Accepting a
    caller-supplied tenant_id would let any visitor impersonate another tenant.
    """
    bot_id: str = Field(..., description="Public UUID of the bot configuration to use.")
    session_id: str = Field(..., min_length=1, max_length=128,
                            description="Unique session identifier for this conversation.")
    message: str = Field(..., min_length=1, max_length=4000,
                         description="The user's chat message.")
    conversation_id: Optional[str] = Field(None, description="Existing conversation UUID to continue.")

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
    # Branding (§08: logo, colors, placement, launcher style)
    primary_color: Optional[str] = "#E6007E"
    secondary_color: Optional[str] = "#1A1B3A"
    logo_url: Optional[str] = None
    placement: Optional[str] = "bottom-right"
    launcher_label: Optional[str] = None

class HealthResponse(BaseModel):
    """Health check response."""
    status: str
    version: str
    service: str


# ── Admin: bots ───────────────────────────────────────────────────────────────

class BotSummary(BaseModel):
    """A bot as listed on the admin dashboard."""
    id: str
    name: str
    status: Optional[str] = "active"
    welcome_message: Optional[str] = None
    booking_url: Optional[str] = None
    support_mode_enabled: bool = True
    sales_mode_enabled: bool = True
    created_at: Optional[Any] = None

class BotCreateRequest(BaseModel):
    """Creates a new bot under the caller's tenant."""
    name: str = Field(..., min_length=1, max_length=120)
    welcome_message: Optional[str] = None
    system_personality: Optional[str] = None
    booking_url: Optional[str] = None
    tone: Optional[str] = Field("professional", pattern="^(professional|friendly|formal)$")
    response_language: Optional[str] = "english"
    support_mode_enabled: bool = True
    sales_mode_enabled: bool = True

class BotUpdateRequest(BaseModel):
    """Partial update — only the provided fields are written."""
    name: Optional[str] = Field(None, min_length=1, max_length=120)
    status: Optional[str] = Field(None, pattern="^(active|paused|archived)$")
    welcome_message: Optional[str] = None
    system_personality: Optional[str] = None
    booking_url: Optional[str] = None
    tone: Optional[str] = Field(None, pattern="^(professional|friendly|formal)$")
    response_language: Optional[str] = None
    max_response_length: Optional[str] = Field(None, pattern="^(short|medium|long)$")
    topic_restrictions: Optional[List[str]] = None
    escalation_email: Optional[str] = None
    escalation_phone: Optional[str] = None
    business_hours: Optional[str] = None
    support_mode_enabled: Optional[bool] = None
    sales_mode_enabled: Optional[bool] = None
    primary_color: Optional[str] = None
    secondary_color: Optional[str] = None
    logo_url: Optional[str] = None
    placement: Optional[str] = Field(None, pattern="^(bottom-right|bottom-left)$")
    launcher_label: Optional[str] = None


# ── Admin: domains ────────────────────────────────────────────────────────────

class DomainCreateRequest(BaseModel):
    """Registers an allowed embed domain for a bot."""
    domain: str = Field(..., min_length=3, max_length=253,
                        description="Hostname, e.g. 'example.com' or '*.example.com'.")

class DomainResponse(BaseModel):
    id: str
    bot_id: str
    domain: str
    created_at: Optional[Any] = None


# ── Admin: knowledge ──────────────────────────────────────────────────────────

class KnowledgeIngestRequest(BaseModel):
    """Ingests a block of pasted text into the tenant's knowledge base."""
    title: str = Field(..., min_length=1, max_length=200)
    content: str = Field(..., min_length=20)
    category: Optional[str] = "faq"
    source_url: Optional[str] = None

class KnowledgeDocumentSummary(BaseModel):
    """One ingested document, aggregated from its chunks."""
    document_id: str
    title: Optional[str]
    category: Optional[str]
    source_url: Optional[str]
    chunk_count: int
    created_at: Optional[Any] = None

class KnowledgeIngestResponse(BaseModel):
    document_id: str
    title: str
    chunks_created: int


# ── Admin: conversations, leads, handoffs ─────────────────────────────────────

class MessageResponse(BaseModel):
    id: Optional[str] = None
    role: str
    content: str
    created_at: Optional[Any] = None

class ConversationSummaryResponse(BaseModel):
    id: str
    bot_id: Optional[str] = None
    session_id: Optional[str] = None
    status: Optional[str] = None
    summary: Optional[str] = None
    message_count: Optional[int] = 0
    created_at: Optional[Any] = None

class ConversationDetailResponse(BaseModel):
    id: str
    bot_id: Optional[str] = None
    session_id: Optional[str] = None
    status: Optional[str] = None
    summary: Optional[str] = None
    messages: List[MessageResponse] = []

class LeadResponse(BaseModel):
    id: str
    name: Optional[str] = None
    email: Optional[str] = None
    company: Optional[str] = None
    requirement: Optional[str] = None
    lead_stage: Optional[str] = None
    services_of_interest: Optional[List[str]] = None
    source: Optional[str] = None
    created_at: Optional[Any] = None

class HandoffResponse(BaseModel):
    id: str
    reason: Optional[str] = None
    summary: Optional[str] = None
    status: Optional[str] = None
    created_at: Optional[Any] = None

class HandoffUpdateRequest(BaseModel):
    status: str = Field(..., pattern="^(pending|in_progress|resolved)$")


# ── Admin: overview ───────────────────────────────────────────────────────────

class DashboardStats(BaseModel):
    """Headline numbers for the dashboard overview screen."""
    total_conversations: int = 0
    total_messages: int = 0
    total_leads: int = 0
    pending_handoffs: int = 0
    total_chunks: int = 0
    total_bots: int = 0
    total_cost_usd: float = 0.0
    avg_latency_ms: int = 0
    model_distribution: Dict[str, int] = {}
