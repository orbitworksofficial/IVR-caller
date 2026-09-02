import { render, h } from "preact";
import { Widget } from "./Widget";
import { styles } from "./styles";
import type { EmbedOptions, WidgetConfig } from "./types";

/**
 * Entry point for the embeddable widget.
 *
 * The customer adds one tag to their site:
 *
 *   <script src="https://chat.orb-itworks.com/widget.js"
 *           data-bot-id="bot_abc123"></script>
 *
 * Everything else — branding, greeting, behavior — is resolved at runtime from
 * that bot id, so the same bundle serves every tenant with no per-site code.
 */

const MOUNT_ID = "orbitworks-chat-widget";

/** Locates the <script> tag that loaded this bundle, to read its data-* attrs. */
function findScriptTag(): HTMLScriptElement | null {
  // document.currentScript is correct during synchronous execution; the query
  // fallback covers async/deferred loading where currentScript is null.
  const current = document.currentScript as HTMLScriptElement | null;
  if (current?.dataset.botId) return current;

  const candidates = document.querySelectorAll<HTMLScriptElement>("script[data-bot-id]");
  return candidates.length > 0 ? candidates[candidates.length - 1] : null;
}

/** Derives the API origin from the script's own src when not given explicitly. */
function deriveApiUrl(script: HTMLScriptElement | null): string {
  const explicit = script?.dataset.apiUrl;
  if (explicit) return explicit.replace(/\/$/, "");

  if (script?.src) {
    try {
      return new URL(script.src).origin;
    } catch {
      /* fall through to the default below */
    }
  }
  return window.location.origin;
}

function readOptions(): EmbedOptions | null {
  const script = findScriptTag();
  const botId = script?.dataset.botId;

  if (!botId) {
    console.error(
      "[OrbitWorks Chat] Missing data-bot-id on the embed script. " +
        'Add it like: <script src="…/widget.js" data-bot-id="YOUR_BOT_ID"></script>'
    );
    return null;
  }

  return {
    botId,
    apiUrl: deriveApiUrl(script),
    autoOpen: script?.dataset.autoOpen === "true",
  };
}

function mount(options: EmbedOptions): void {
  // Guard against the script being included twice on one page.
  if (document.getElementById(MOUNT_ID)) {
    console.warn("[OrbitWorks Chat] Widget is already mounted on this page.");
    return;
  }

  const host = document.createElement("div");
  host.id = MOUNT_ID;
  document.body.appendChild(host);

  // Shadow DOM isolation (§03): the host page's CSS cannot restyle the widget,
  // and the widget's CSS cannot leak onto the host page.
  const shadow = host.attachShadow({ mode: "open" });

  const styleEl = document.createElement("style");
  // Painted with defaults first, then repainted once the bot's real brand
  // colors arrive, so the launcher never flashes unstyled.
  styleEl.textContent = styles("#E6007E", "#1A1B3A");
  shadow.appendChild(styleEl);

  const container = document.createElement("div");
  shadow.appendChild(container);

  const onConfigLoaded = (config: WidgetConfig) => {
    styleEl.textContent = styles(
      config.primary_color || "#E6007E",
      config.secondary_color || "#1A1B3A"
    );
  };

  render(h(Widget, { options, onConfigLoaded }), container);
}

function init(): void {
  const options = readOptions();
  if (!options) return;

  if (document.body) {
    mount(options);
  } else {
    document.addEventListener("DOMContentLoaded", () => mount(options), { once: true });
  }
}

init();

// Expose a tiny programmatic API for hosts that want to mount manually.
(window as any).OrbitWorksChat = {
  mount: (options: Partial<EmbedOptions> & { botId: string }) =>
    mount({
      apiUrl: window.location.origin,
      autoOpen: false,
      ...options,
    }),
};
