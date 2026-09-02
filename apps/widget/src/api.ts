import type { WidgetConfig } from "./types";

const SESSION_KEY = "orbitworks_chat_session";
const CONVERSATION_KEY = "orbitworks_chat_conversation";

/**
 * Returns a stable per-visitor session id, persisted so a page refresh resumes
 * the same conversation. Falls back to an in-memory value when storage is
 * unavailable (private browsing, blocked cookies) rather than throwing.
 */
let memorySession: string | null = null;

function readStorage(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function writeStorage(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    /* storage blocked — in-memory fallback already covers this */
  }
}

function randomId(): string {
  try {
    return crypto.randomUUID();
  } catch {
    return `s_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 10)}`;
  }
}

export function getSessionId(): string {
  const stored = readStorage(SESSION_KEY);
  if (stored) return stored;
  if (memorySession) return memorySession;

  const fresh = randomId();
  memorySession = fresh;
  writeStorage(SESSION_KEY, fresh);
  return fresh;
}

export function getConversationId(): string | null {
  return readStorage(CONVERSATION_KEY);
}

export function setConversationId(id: string): void {
  writeStorage(CONVERSATION_KEY, id);
}

export function clearConversation(): void {
  try {
    window.localStorage.removeItem(CONVERSATION_KEY);
  } catch {
    /* nothing to clear */
  }
}

/** Fetches the bot's public branding + behavior configuration. */
export async function fetchConfig(
  apiUrl: string,
  botId: string
): Promise<WidgetConfig> {
  const response = await fetch(
    `${apiUrl}/widget/config?bot_id=${encodeURIComponent(botId)}`,
    { method: "GET" }
  );

  if (!response.ok) {
    const detail = await response.json().catch(() => ({}));
    throw new Error(detail.detail || `Could not load chat configuration.`);
  }

  return response.json();
}

export interface StreamHandlers {
  onMeta?: (conversationId: string) => void;
  onToken: (text: string) => void;
  onDone: (meta: { grounded: boolean; model: string }) => void;
  onError: (message: string) => void;
}

/**
 * Streams one assistant reply over SSE.
 *
 * Uses fetch + ReadableStream rather than EventSource: EventSource cannot be
 * aborted cleanly mid-stream, and we need that to cancel an in-flight reply
 * when the visitor closes the panel.
 *
 * Returns an abort function.
 */
export function streamChat(
  apiUrl: string,
  botId: string,
  message: string,
  handlers: StreamHandlers
): () => void {
  const controller = new AbortController();

  const params = new URLSearchParams({
    bot_id: botId,
    session_id: getSessionId(),
    message,
  });
  const conversationId = getConversationId();
  if (conversationId) params.set("conversation_id", conversationId);

  (async () => {
    try {
      const response = await fetch(`${apiUrl}/chat/stream?${params}`, {
        method: "GET",
        headers: { Accept: "text/event-stream" },
        signal: controller.signal,
      });

      if (!response.ok) {
        let detail = "Sorry, I couldn't send that message.";
        try {
          const body = await response.json();
          if (body.detail) detail = body.detail;
        } catch {
          /* keep the generic message */
        }
        handlers.onError(detail);
        return;
      }

      if (!response.body) {
        handlers.onError("Streaming is not supported in this browser.");
        return;
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });

        // SSE frames are separated by a blank line. Keep the trailing partial
        // frame in the buffer until its terminator arrives.
        const frames = buffer.split("\n\n");
        buffer = frames.pop() ?? "";

        for (const frame of frames) {
          if (!frame.trim()) continue;

          let eventName = "message";
          let data = "";
          for (const line of frame.split("\n")) {
            if (line.startsWith("event:")) eventName = line.slice(6).trim();
            else if (line.startsWith("data:")) data += line.slice(5).trim();
          }
          if (!data) continue;

          let payload: any;
          try {
            payload = JSON.parse(data);
          } catch {
            continue;
          }

          if (eventName === "meta" && payload.conversation_id) {
            setConversationId(payload.conversation_id);
            handlers.onMeta?.(payload.conversation_id);
          } else if (eventName === "token" && typeof payload.text === "string") {
            handlers.onToken(payload.text);
          } else if (eventName === "done") {
            handlers.onDone({
              grounded: !!payload.grounded,
              model: payload.model || "",
            });
          } else if (eventName === "error") {
            handlers.onError(payload.message || "Something went wrong.");
          }
        }
      }
    } catch (error: any) {
      // An intentional abort is not an error worth showing the visitor.
      if (error?.name === "AbortError") return;
      handlers.onError("Connection lost. Please check your network and try again.");
    }
  })();

  return () => controller.abort();
}
