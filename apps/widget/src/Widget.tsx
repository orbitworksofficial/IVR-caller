import { useState, useRef, useEffect, useCallback } from "preact/hooks";
import type { ChatMessage, EmbedOptions, WidgetConfig } from "./types";
import {
  fetchConfig, streamChat, clearConversation, getConversationId,
} from "./api";

/**
 * Renders a small, safe subset of markdown: bold, italic, inline code, links
 * and bullet lists. The model's output is escaped first, so nothing it emits
 * can inject markup into the host page.
 */
function renderMarkdown(text: string): string {
  const escaped = text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");

  let html = escaped
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>")
    // Only linkify http(s) URLs — never javascript: or data: schemes.
    .replace(
      /(https?:\/\/[^\s<]+)/g,
      '<a href="$1" target="_blank" rel="noopener noreferrer">$1</a>'
    );

  // Group consecutive bullet lines into a single <ul>. The bubble uses
  // white-space: pre-wrap, so list markup is emitted without surrounding
  // newlines — otherwise every item would gain a blank line above it.
  const lines = html.split("\n");
  const out: string[] = [];
  let inList = false;

  const closeList = () => {
    if (inList) {
      out[out.length - 1] += "</ul>";
      inList = false;
    }
  };

  for (const line of lines) {
    const bullet = line.match(/^\s*[-*•]\s+(.*)$/);
    if (bullet) {
      if (!inList) {
        // Attach the list opener to the previous line so no newline is emitted.
        if (out.length > 0) out[out.length - 1] += "<ul>";
        else out.push("<ul>");
        inList = true;
        out.push(`<li>${bullet[1]}</li>`);
      } else {
        out[out.length - 1] += `<li>${bullet[1]}</li>`;
      }
    } else {
      closeList();
      out.push(line);
    }
  }
  closeList();

  return out.join("\n");
}

function newId(): string {
  return `m_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`;
}

const SendIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
       stroke-linecap="round" stroke-linejoin="round">
    <line x1="22" y1="2" x2="11" y2="13" /><polygon points="22 2 15 22 11 13 2 9 22 2" />
  </svg>
);

const ChatIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
       stroke-linecap="round" stroke-linejoin="round">
    <path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z" />
  </svg>
);

const CloseIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
       stroke-linecap="round" stroke-linejoin="round">
    <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
  </svg>
);

const RefreshIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
       stroke-linecap="round" stroke-linejoin="round">
    <polyline points="1 4 1 10 7 10" />
    <path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10" />
  </svg>
);

const CalendarIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
       stroke-linecap="round" stroke-linejoin="round">
    <rect x="3" y="4" width="18" height="18" rx="2" />
    <line x1="16" y1="2" x2="16" y2="6" /><line x1="8" y1="2" x2="8" y2="6" />
    <line x1="3" y1="10" x2="21" y2="10" />
  </svg>
);

interface Props {
  options: EmbedOptions;
  onConfigLoaded: (config: WidgetConfig) => void;
}

export function Widget({ options, onConfigLoaded }: Props) {
  const [open, setOpen] = useState(options.autoOpen);
  const [config, setConfig] = useState<WidgetConfig | null>(null);
  const [configError, setConfigError] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);

  const messagesRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const abortRef = useRef<(() => void) | null>(null);

  // ── Load configuration once ────────────────────────────────────────────
  useEffect(() => {
    let cancelled = false;

    fetchConfig(options.apiUrl, options.botId)
      .then((loaded) => {
        if (cancelled) return;
        setConfig(loaded);
        onConfigLoaded(loaded);

        // A returning visitor resumes their thread; the greeting is only for
        // a genuinely new conversation.
        if (!getConversationId() && loaded.welcome_message) {
          setMessages([{
            id: newId(),
            role: "assistant",
            content: loaded.welcome_message,
          }]);
        }
      })
      .catch((error: Error) => {
        if (cancelled) return;
        setConfigError(error.message);
      });

    return () => { cancelled = true; };
  }, [options.apiUrl, options.botId]);

  // ── Keep the transcript pinned to the newest message ───────────────────
  useEffect(() => {
    const node = messagesRef.current;
    if (node) node.scrollTop = node.scrollHeight;
  }, [messages]);

  useEffect(() => {
    if (open) textareaRef.current?.focus();
  }, [open]);

  // Cancel any in-flight stream when the widget unmounts.
  useEffect(() => () => abortRef.current?.(), []);

  const send = useCallback(() => {
    const text = input.trim();
    if (!text || sending || !config) return;

    const userMessage: ChatMessage = { id: newId(), role: "user", content: text };
    const assistantId = newId();

    setMessages((prev) => [
      ...prev,
      userMessage,
      { id: assistantId, role: "assistant", content: "", streaming: true },
    ]);
    setInput("");
    setSending(true);

    // Reset the textarea's auto-grown height.
    if (textareaRef.current) textareaRef.current.style.height = "auto";

    const updateAssistant = (patch: Partial<ChatMessage>) => {
      setMessages((prev) =>
        prev.map((m) => (m.id === assistantId ? { ...m, ...patch } : m))
      );
    };

    abortRef.current = streamChat(options.apiUrl, options.botId, text, {
      onToken: (token) => {
        setMessages((prev) =>
          prev.map((m) =>
            m.id === assistantId ? { ...m, content: m.content + token } : m
          )
        );
      },
      onDone: () => {
        updateAssistant({ streaming: false });
        setSending(false);
        abortRef.current = null;
      },
      onError: (errorMessage) => {
        updateAssistant({
          content: errorMessage,
          streaming: false,
          error: true,
        });
        setSending(false);
        abortRef.current = null;
      },
    });
  }, [input, sending, config, options.apiUrl, options.botId]);

  const handleKeyDown = (event: KeyboardEvent) => {
    // Enter sends; Shift+Enter inserts a newline.
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      send();
    }
  };

  const handleInput = (event: Event) => {
    const target = event.currentTarget as HTMLTextAreaElement;
    setInput(target.value);
    // Grow the textarea with its content, up to the CSS max-height.
    target.style.height = "auto";
    target.style.height = `${Math.min(target.scrollHeight, 110)}px`;
  };

  const resetConversation = () => {
    abortRef.current?.();
    abortRef.current = null;
    clearConversation();
    setSending(false);
    setMessages(
      config?.welcome_message
        ? [{ id: newId(), role: "assistant", content: config.welcome_message }]
        : []
    );
  };

  const closePanel = () => {
    // Abandon an in-flight reply rather than leaving the connection open.
    abortRef.current?.();
    abortRef.current = null;
    setSending(false);
    setOpen(false);
  };

  const placement = config?.placement === "bottom-left" ? "left" : "right";
  const botName = config?.bot_name ?? "Chat";
  const initial = botName.charAt(0).toUpperCase();
  const launcherLabel = config?.launcher_label;

  // The booking CTA appears once the assistant has actually offered a call.
  const showBooking =
    !!config?.booking_url &&
    messages.some(
      (m) =>
        m.role === "assistant" &&
        !m.streaming &&
        /\b(book|schedule|discovery call|strategy call|calendly)\b/i.test(m.content)
    );

  return (
    <div class={`root ${placement} ${open ? "open" : ""}`}>
      {open && (
        <div class="panel" role="dialog" aria-label={`${botName} chat`}>
          <div class="header">
            <div class="header-avatar">
              {config?.logo_url
                ? <img src={config.logo_url} alt="" />
                : initial}
            </div>
            <div class="header-text">
              <div class="header-name">{botName}</div>
              <div class="header-status">
                <span class="status-dot" />
                {sending ? "Typing…" : "Online"}
              </div>
            </div>
            <div class="header-actions">
              <button class="icon-btn" onClick={resetConversation}
                      aria-label="Start a new conversation" title="New conversation">
                <RefreshIcon />
              </button>
              <button class="icon-btn" onClick={closePanel}
                      aria-label="Close chat" title="Close">
                <CloseIcon />
              </button>
            </div>
          </div>

          {configError && (
            <div class="error-banner" style="margin-top:12px">{configError}</div>
          )}

          <div class="messages" ref={messagesRef} role="log" aria-live="polite">
            {messages.map((message) => {
              // An assistant bubble that is still empty shows the typing dots.
              if (message.role === "assistant" && message.streaming && !message.content) {
                return (
                  <div key={message.id} class="msg assistant">
                    <div class="typing"><span /><span /><span /></div>
                  </div>
                );
              }
              return (
                <div
                  key={message.id}
                  class={`msg ${message.role}${message.error ? " error" : ""}`}
                  dangerouslySetInnerHTML={{ __html: renderMarkdown(message.content) }}
                />
              );
            })}

            {showBooking && config?.booking_url && (
              <a class="book-btn" href={config.booking_url}
                 target="_blank" rel="noopener noreferrer">
                <CalendarIcon /> Book a free call
              </a>
            )}
          </div>

          <div class="composer">
            <textarea
              ref={textareaRef}
              value={input}
              placeholder={configError ? "Chat unavailable" : "Type your message…"}
              rows={1}
              disabled={!config || !!configError}
              onInput={handleInput}
              onKeyDown={handleKeyDown}
              aria-label="Message"
            />
            <button
              class="send-btn"
              onClick={send}
              disabled={!input.trim() || sending || !config}
              aria-label="Send message"
            >
              <SendIcon />
            </button>
          </div>

          <div class="footer">AI assistant — responses may be inaccurate</div>
        </div>
      )}

      <button
        class={`launcher ${launcherLabel ? "" : "icon-only"}`}
        onClick={() => (open ? closePanel() : setOpen(true))}
        aria-label={open ? "Close chat" : "Open chat"}
        aria-expanded={open}
      >
        {open ? <CloseIcon /> : <ChatIcon />}
        {!open && launcherLabel ? <span>{launcherLabel}</span> : null}
      </button>
    </div>
  );
}
