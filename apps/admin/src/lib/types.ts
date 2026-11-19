export interface Admin {
  user_id: string;
  email: string | null;
  tenant_id: string;
  own_tenant_id: string;
  role: "admin" | "client";
  is_super_admin: boolean;
  is_impersonating: boolean;
}

export interface TenantSummary {
  id: string;
  name: string;
  contact_email?: string | null;
  industry?: string | null;
  website?: string | null;
  created_at?: string;
  bot_count: number;
  chunk_count: number;
  conversation_count: number;
  lead_count: number;
  pending_handoffs: number;
  admin_count: number;
}

export interface AdminAccount {
  id: string;
  user_id: string;
  tenant_id: string;
  tenant_name?: string | null;
  email?: string | null;
  role: "admin" | "client";
  is_super_admin: boolean;
  created_at?: string;
}

export interface CreateTenantResult {
  tenant_id: string;
  bot_id: string;
  user_id: string;
  admin_email: string;
  embed_snippet: string;
}

export interface Bot {
  id: string;
  tenant_id?: string;
  name: string;
  status?: string;
  welcome_message?: string | null;
  system_personality?: string | null;
  booking_url?: string | null;
  tone?: string;
  response_language?: string;
  max_response_length?: string;
  topic_restrictions?: string[] | null;
  escalation_email?: string | null;
  escalation_phone?: string | null;
  business_hours?: string | null;
  support_mode_enabled: boolean;
  sales_mode_enabled: boolean;
  primary_color?: string | null;
  secondary_color?: string | null;
  logo_url?: string | null;
  placement?: string | null;
  launcher_label?: string | null;
  created_at?: string;
}

export interface BotDomain {
  id: string;
  bot_id: string;
  domain: string;
  created_at?: string;
}

export interface KnowledgeDocument {
  document_id: string;
  title: string | null;
  category: string | null;
  source_url: string | null;
  chunk_count: number;
  created_at?: string;
}

export interface Message {
  id?: string;
  role: string;
  content: string;
  created_at?: string;
}

export interface Conversation {
  id: string;
  bot_id?: string | null;
  session_id?: string | null;
  status?: string | null;
  summary?: string | null;
  created_at?: string;
}

export interface ConversationDetail extends Conversation {
  messages: Message[];
}

export interface Lead {
  id: string;
  name?: string | null;
  email?: string | null;
  company?: string | null;
  requirement?: string | null;
  lead_stage?: string | null;
  services_of_interest?: string[] | null;
  source?: string | null;
  created_at?: string;
}

export interface Handoff {
  id: string;
  reason?: string | null;
  summary?: string | null;
  status?: string | null;
  created_at?: string;
}

export interface DashboardStats {
  total_conversations: number;
  total_messages: number;
  total_leads: number;
  pending_handoffs: number;
  total_chunks: number;
  total_bots: number;
  total_cost_usd: number;
  avg_latency_ms: number;
  model_distribution: Record<string, number>;
}

export interface PhoneNumber {
  id: string;
  tenant_id: string;
  bot_id?: string | null;
  phone_number: string;
  friendly_name?: string | null;
  direction: "inbound" | "outbound" | "both";
  provider?: string;
  is_active: boolean;
  created_at?: string;
  twilio_webhooks?: { sid: string; voice_url: string; status_callback: string } | null;
}

export interface VoiceProfile {
  id: string;
  tenant_id: string;
  name: string;
  provider: string;
  voice_id: string;
  model_id?: string;
  stability?: number;
  similarity_boost?: number;
  style?: number;
  is_default?: boolean;
  created_at?: string;
}

export interface IvrBotConfig {
  id?: string;
  tenant_id?: string;
  bot_id?: string;
  phone_number_id?: string | null;
  voice_profile_id?: string | null;
  voice_id?: string | null;
  voice_model_id?: string | null;
  voice_stability?: number | null;
  voice_similarity_boost?: number | null;
  voice_style?: number | null;
  stt_model?: string | null;
  stt_endpointing_ms?: number | null;
  stt_utterance_end_ms?: number | null;
  call_recording_enabled?: boolean;
  recording_disclosure_text?: string | null;
  recording_retention_days?: number | null;
  outbound_caller_id?: string | null;
  calling_window_start?: string | null;
  calling_window_end?: string | null;
  calling_timezone?: string | null;
  max_call_attempts?: number | null;
  retry_interval_hours?: number | null;
  voicemail_message?: string | null;
  abstain_phrase?: string | null;
  filler_phrase?: string | null;
  voice_script?: Record<string, unknown> | null;
}

export interface CallRecord {
  id: string;
  tenant_id: string;
  bot_id?: string | null;
  direction: "inbound" | "outbound";
  from_number: string;
  to_number: string;
  twilio_call_sid?: string | null;
  started_at?: string | null;
  answered_at?: string | null;
  ended_at?: string | null;
  duration_s?: number | null;
  status?: string | null;
  outcome?: string | null;
  disposition?: string | null;
  model_used?: string | null;
  turn_count?: number | null;
  call_summary?: string | null;
  recording_url?: string | null;
  estimated_cost_usd?: number | null;
}

export interface CallTurn {
  id: string;
  speaker: "caller" | "agent";
  text: string;
  turn_index: number;
  latency_ms?: number | null;
  model_used?: string | null;
}

export interface CallDetailResponse {
  call: CallRecord;
  turns: CallTurn[];
  transcript?: { full_transcript: string; word_count?: number } | null;
  recordings: {
    id: string;
    recording_sid?: string | null;
    recording_url?: string | null;
    storage_path?: string | null;
    duration_s?: number | null;
    status?: string | null;
    expires_at?: string | null;
  }[];
}

export interface ConsentRecord {
  id: string;
  phone_number: string;
  consent_type: string;
  source: string;
  granted_at?: string | null;
  revoked_at?: string | null;
  is_active?: boolean;
  evidence_text?: string | null;
}

export interface DncEntry {
  id: string;
  phone_number: string;
  source: string;
  reason?: string | null;
  added_at?: string | null;
}

export interface CallCampaign {
  id: string;
  bot_id?: string | null;
  name: string;
  status: "draft" | "active" | "paused" | "completed";
  campaign_type: string;
  call_reason?: string | null;
  max_attempts: number;
  max_concurrent?: number;
  contact_counts?: Record<string, number>;
  created_at?: string | null;
}
