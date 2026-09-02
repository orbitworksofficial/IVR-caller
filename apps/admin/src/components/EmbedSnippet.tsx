"use client";

import { useState } from "react";
import { IconCopy, IconCheck } from "./Icons";
import { Alert } from "./ui";

const WIDGET_URL =
  process.env.NEXT_PUBLIC_WIDGET_URL || "http://localhost:8000/widget.js";
const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

/**
 * Renders the one-line embed snippet for a bot, with a copy button.
 * This is the artifact the customer pastes into their own website.
 */
export function EmbedSnippet({
  botId, botName,
}: {
  botId: string;
  botName?: string;
}) {
  const [copied, setCopied] = useState(false);

  // data-api-url is emitted only when the API and the widget are served from
  // different origins; otherwise the widget infers it from its own src.
  const needsApiUrl = (() => {
    try {
      return new URL(WIDGET_URL).origin !== new URL(API_URL).origin;
    } catch {
      return true;
    }
  })();

  const snippet = needsApiUrl
    ? `<script
  src="${WIDGET_URL}"
  data-bot-id="${botId}"
  data-api-url="${API_URL}"
  async
></script>`
    : `<script
  src="${WIDGET_URL}"
  data-bot-id="${botId}"
  async
></script>`;

  async function copy() {
    try {
      await navigator.clipboard.writeText(snippet);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Clipboard needs a secure context; the code is selectable as a fallback.
      setCopied(false);
    }
  }

  return (
    <div className="card" style={{ maxWidth: 780 }}>
      <div className="card-header">
        <div>
          <div className="card-title">Embed on your website</div>
          <div className="card-desc">
            Paste this before the closing <code>&lt;/body&gt;</code> tag. Works on
            any site — WordPress, Shopify, Webflow, React, plain HTML.
          </div>
        </div>
        <button className="btn btn-primary btn-sm" onClick={copy}>
          {copied ? <><IconCheck /> Copied</> : <><IconCopy /> Copy</>}
        </button>
      </div>

      <div className="card-body">
        <pre className="code-block">{snippet}</pre>

        <div style={{ marginTop: 20 }}>
          <h3 style={{ marginBottom: 10 }}>Options</h3>
          <div className="table-wrap">
            <table>
              <thead>
                <tr><th>Attribute</th><th>Required</th><th>Description</th></tr>
              </thead>
              <tbody>
                <tr>
                  <td className="cell-mono">data-bot-id</td>
                  <td><span className="badge badge-danger">Required</span></td>
                  <td>Identifies which bot to load{botName ? ` — this is ${botName}` : ""}.</td>
                </tr>
                <tr>
                  <td className="cell-mono">data-api-url</td>
                  <td><span className="badge badge-neutral">Optional</span></td>
                  <td>API origin. Defaults to wherever the script is served from.</td>
                </tr>
                <tr>
                  <td className="cell-mono">data-auto-open</td>
                  <td><span className="badge badge-neutral">Optional</span></td>
                  <td>Set to <code>true</code> to open the panel on page load.</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>

        <Alert kind="info">
          The snippet contains no secrets — only a public bot id. Restrict which
          sites may use it on the <strong>Domains</strong> tab.
        </Alert>

        <div style={{ marginTop: 4 }}>
          <h3 style={{ marginBottom: 8 }}>Platform notes</h3>
          <ul style={{
            margin: 0, paddingLeft: 20, fontSize: 13.5,
            color: "var(--text-muted)", lineHeight: 1.8,
          }}>
            <li><strong>WordPress</strong> — Appearance → Theme File Editor → footer.php, or use a &ldquo;header and footer scripts&rdquo; plugin.</li>
            <li><strong>Shopify</strong> — Online Store → Themes → Edit code → theme.liquid, before <code>&lt;/body&gt;</code>.</li>
            <li><strong>Webflow</strong> — Project Settings → Custom Code → Footer Code.</li>
            <li><strong>Next.js / React</strong> — add the tag in your root layout, or load it with the <code>next/script</code> component.</li>
            <li><strong>Squarespace / Wix</strong> — Settings → Advanced → Code Injection (Footer).</li>
          </ul>
        </div>
      </div>
    </div>
  );
}
