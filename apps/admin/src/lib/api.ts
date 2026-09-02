import { createClient } from "./supabase";
import type {
  Admin, Bot, BotDomain, KnowledgeDocument, Conversation,
  ConversationDetail, Lead, Handoff, DashboardStats,
} from "./types";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

/** Thrown for any non-2xx response, carrying the backend's detail message. */
export class ApiError extends Error {
  constructor(message: string, public status: number) {
    super(message);
    this.name = "ApiError";
  }
}

/**
 * Calls the FastAPI admin API with the current Supabase access token.
 * The backend resolves that token to the caller's tenant, so no tenant_id is
 * ever sent from the browser.
 */
async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const supabase = createClient();
  const { data } = await supabase.auth.getSession();
  const token = data.session?.access_token;

  if (!token) {
    throw new ApiError("Your session has expired. Please sign in again.", 401);
  }

  const headers = new Headers(init.headers);
  headers.set("Authorization", `Bearer ${token}`);
  // FormData sets its own multipart boundary — never override it.
  if (!(init.body instanceof FormData) && init.body) {
    headers.set("Content-Type", "application/json");
  }

  let response: Response;
  try {
    response = await fetch(`${API_URL}${path}`, { ...init, headers });
  } catch {
    throw new ApiError(
      `Could not reach the API at ${API_URL}. Is the backend running?`,
      0
    );
  }

  if (response.status === 204) return undefined as T;

  if (!response.ok) {
    let detail = `Request failed (${response.status}).`;
    try {
      const body = await response.json();
      if (body.detail) {
        detail = typeof body.detail === "string"
          ? body.detail
          : JSON.stringify(body.detail);
      }
    } catch {
      /* keep the status-based message */
    }
    throw new ApiError(detail, response.status);
  }

  return response.json();
}

export const api = {
  // Session
  me: () => request<Admin>("/admin/me"),
  stats: () => request<DashboardStats>("/admin/stats"),

  // Bots
  listBots: () => request<Bot[]>("/admin/bots"),
  getBot: (id: string) => request<Bot>(`/admin/bots/${id}`),
  createBot: (payload: Partial<Bot>) =>
    request<Bot>("/admin/bots", { method: "POST", body: JSON.stringify(payload) }),
  updateBot: (id: string, payload: Partial<Bot>) =>
    request<Bot>(`/admin/bots/${id}`, { method: "PATCH", body: JSON.stringify(payload) }),
  deleteBot: (id: string) =>
    request<void>(`/admin/bots/${id}`, { method: "DELETE" }),

  // Domains
  listDomains: (botId: string) =>
    request<BotDomain[]>(`/admin/bots/${botId}/domains`),
  addDomain: (botId: string, domain: string) =>
    request<BotDomain>(`/admin/bots/${botId}/domains`, {
      method: "POST",
      body: JSON.stringify({ domain }),
    }),
  removeDomain: (botId: string, domainId: string) =>
    request<void>(`/admin/bots/${botId}/domains/${domainId}`, { method: "DELETE" }),

  // Knowledge
  listKnowledge: () => request<KnowledgeDocument[]>("/admin/knowledge"),
  ingestText: (payload: {
    title: string; content: string; category?: string; source_url?: string;
  }) =>
    request<{ document_id: string; title: string; chunks_created: number }>(
      "/admin/knowledge",
      { method: "POST", body: JSON.stringify(payload) }
    ),
  uploadKnowledge: (file: File, title: string, category: string) => {
    const form = new FormData();
    form.append("file", file);
    form.append("title", title);
    form.append("category", category);
    return request<{ document_id: string; title: string; chunks_created: number }>(
      "/admin/knowledge/upload",
      { method: "POST", body: form }
    );
  },
  deleteKnowledge: (documentId: string) =>
    request<void>(`/admin/knowledge/${documentId}`, { method: "DELETE" }),

  // Conversations
  listConversations: (limit = 50) =>
    request<Conversation[]>(`/admin/conversations?limit=${limit}`),
  getConversation: (id: string) =>
    request<ConversationDetail>(`/admin/conversations/${id}`),

  // Leads & handoffs
  listLeads: () => request<Lead[]>("/admin/leads"),
  listHandoffs: () => request<Handoff[]>("/admin/handoffs"),
  updateHandoff: (id: string, status: string) =>
    request<Handoff>(`/admin/handoffs/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ status }),
    }),
};
