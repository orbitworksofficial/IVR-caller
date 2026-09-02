/** Public bot configuration returned by GET /widget/config. */
export interface WidgetConfig {
  bot_id: string;
  bot_name: string;
  welcome_message: string | null;
  booking_url: string | null;
  support_mode_enabled: boolean;
  sales_mode_enabled: boolean;
  primary_color: string;
  secondary_color: string;
  logo_url: string | null;
  placement: "bottom-right" | "bottom-left";
  launcher_label: string | null;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  /** True while tokens are still streaming into this message. */
  streaming?: boolean;
  /** Set when the turn failed, so the UI can offer a retry. */
  error?: boolean;
}

/** Settings read from the embedding <script> tag's data-* attributes. */
export interface EmbedOptions {
  botId: string;
  apiUrl: string;
  /** Opens the panel automatically on first load. */
  autoOpen: boolean;
}
