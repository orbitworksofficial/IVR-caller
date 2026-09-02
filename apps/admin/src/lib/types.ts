export interface Admin {
  user_id: string;
  email: string | null;
  tenant_id: string;
  role: string;
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
