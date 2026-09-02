"use client";

import { useEffect, useState, use } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Shell } from "@/components/Shell";
import {
  Alert, LoadingState, Spinner, formatDate,
} from "@/components/ui";
import { IconBack, IconTrash, IconPlus, IconGlobe } from "@/components/Icons";
import { EmbedSnippet } from "@/components/EmbedSnippet";
import { api, ApiError } from "@/lib/api";
import type { Bot, BotDomain } from "@/lib/types";

type Tab = "behavior" | "branding" | "domains" | "embed";

export default function BotDetailPage({
  params,
}: {
  params: Promise<{ botId: string }>;
}) {
  const { botId } = use(params);
  const router = useRouter();

  const [bot, setBot] = useState<Bot | null>(null);
  const [domains, setDomains] = useState<BotDomain[]>([]);
  const [tab, setTab] = useState<Tab>("behavior");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [saving, setSaving] = useState(false);

  // Local draft of the editable fields.
  const [form, setForm] = useState<Partial<Bot>>({});

  useEffect(() => {
    Promise.all([api.getBot(botId), api.listDomains(botId)])
      .then(([botData, domainData]) => {
        setBot(botData);
        setForm(botData);
        setDomains(domainData);
      })
      .catch((e: ApiError) => setError(e.message))
      .finally(() => setLoading(false));
  }, [botId]);

  function update<K extends keyof Bot>(key: K, value: Bot[K]) {
    setForm((prev) => ({ ...prev, [key]: value }));
    setSaved(false);
  }

  async function save() {
    setSaving(true);
    setError(null);

    // Send only what the API accepts as editable, so unknown columns from the
    // full bot record are never echoed back as updates.
    const payload: Partial<Bot> = {
      name: form.name,
      status: form.status,
      welcome_message: form.welcome_message,
      system_personality: form.system_personality,
      booking_url: form.booking_url,
      tone: form.tone,
      response_language: form.response_language,
      max_response_length: form.max_response_length,
      escalation_email: form.escalation_email,
      escalation_phone: form.escalation_phone,
      business_hours: form.business_hours,
      support_mode_enabled: form.support_mode_enabled,
      sales_mode_enabled: form.sales_mode_enabled,
      primary_color: form.primary_color,
      secondary_color: form.secondary_color,
      logo_url: form.logo_url,
      placement: form.placement,
      launcher_label: form.launcher_label,
    };

    try {
      const updated = await api.updateBot(botId, payload);
      setBot(updated);
      setSaved(true);
      setTimeout(() => setSaved(false), 3000);
    } catch (e) {
      setError((e as ApiError).message);
    } finally {
      setSaving(false);
    }
  }

  async function archive() {
    if (!confirm("Archive this bot? It will stop responding on your website.")) return;
    try {
      await api.deleteBot(botId);
      router.push("/bots");
    } catch (e) {
      setError((e as ApiError).message);
    }
  }

  if (loading) {
    return <Shell title="Bot"><LoadingState label="Loading configuration…" /></Shell>;
  }

  if (!bot) {
    return (
      <Shell title="Bot">
        <Alert kind="error">{error || "Bot not found."}</Alert>
        <Link href="/bots" className="btn btn-secondary"><IconBack /> Back to bots</Link>
      </Shell>
    );
  }

  return (
    <Shell
      title={bot.name}
      subtitle="Everything here is stored in the database — no redeploy needed."
      actions={
        <>
          <Link href="/bots" className="btn btn-ghost"><IconBack /> Back</Link>
          <button className="btn btn-primary" onClick={save} disabled={saving}>
            {saving ? <><Spinner /> Saving…</> : "Save changes"}
          </button>
        </>
      }
    >
      {error && <Alert kind="error">{error}</Alert>}
      {saved && <Alert kind="success">Changes saved. The widget picks them up within a minute.</Alert>}

      <div className="tabs">
        {([
          ["behavior", "Behavior"],
          ["branding", "Branding"],
          ["domains", "Domains"],
          ["embed", "Embed"],
        ] as [Tab, string][]).map(([key, label]) => (
          <button
            key={key}
            className={`tab${tab === key ? " active" : ""}`}
            onClick={() => setTab(key)}
          >
            {label}
          </button>
        ))}
      </div>

      {tab === "behavior" && (
        <BehaviorTab form={form} update={update} onArchive={archive} />
      )}
      {tab === "branding" && <BrandingTab form={form} update={update} />}
      {tab === "domains" && (
        <DomainsTab botId={botId} domains={domains} setDomains={setDomains} />
      )}
      {tab === "embed" && <EmbedSnippet botId={botId} botName={bot.name} />}
    </Shell>
  );
}

// ── Behavior ─────────────────────────────────────────────────────────────────

function BehaviorTab({
  form, update, onArchive,
}: {
  form: Partial<Bot>;
  update: <K extends keyof Bot>(key: K, value: Bot[K]) => void;
  onArchive: () => void;
}) {
  return (
    <div className="grid grid-2">
      <div className="card">
        <div className="card-header">
          <div>
            <div className="card-title">Identity</div>
            <div className="card-desc">How the assistant introduces itself.</div>
          </div>
        </div>
        <div className="card-body">
          <div className="field">
            <label className="label">Business name</label>
            <input className="input" value={form.name ?? ""}
                   onChange={(e) => update("name", e.target.value)} />
          </div>

          <div className="field">
            <label className="label">Welcome message</label>
            <input className="input" value={form.welcome_message ?? ""}
                   onChange={(e) => update("welcome_message", e.target.value)}
                   placeholder="Hi! How can I help you today?" />
          </div>

          <div className="field">
            <label className="label">Business context</label>
            <textarea className="textarea" value={form.system_personality ?? ""}
                      onChange={(e) => update("system_personality", e.target.value)}
                      placeholder="You are the assistant for…" />
            <p className="hint">
              Sets the bot&apos;s role. Factual answers still come only from the
              knowledge base.
            </p>
          </div>

          <div className="field">
            <label className="label">Status</label>
            <select className="select" value={form.status ?? "active"}
                    onChange={(e) => update("status", e.target.value)}>
              <option value="active">Active — responding</option>
              <option value="paused">Paused — not responding</option>
            </select>
          </div>
        </div>
      </div>

      <div className="card">
        <div className="card-header">
          <div>
            <div className="card-title">Modes &amp; tone</div>
            <div className="card-desc">What the bot is allowed to do.</div>
          </div>
        </div>
        <div className="card-body">
          <label className="checkbox-row">
            <input type="checkbox" checked={form.support_mode_enabled ?? true}
                   onChange={(e) => update("support_mode_enabled", e.target.checked)} />
            <div>
              <div className="checkbox-label">Customer support</div>
              <div className="checkbox-desc">
                Answers FAQs from the knowledge base, captures contact details,
                escalates when it doesn&apos;t know.
              </div>
            </div>
          </label>

          <label className="checkbox-row">
            <input type="checkbox" checked={form.sales_mode_enabled ?? true}
                   onChange={(e) => update("sales_mode_enabled", e.target.checked)} />
            <div>
              <div className="checkbox-label">Consultative sales</div>
              <div className="checkbox-desc">
                Discovers needs, qualifies visitors, captures leads and guides
                them to book a call.
              </div>
            </div>
          </label>

          <div className="field" style={{ marginTop: 16 }}>
            <label className="label">Tone</label>
            <select className="select" value={form.tone ?? "professional"}
                    onChange={(e) => update("tone", e.target.value)}>
              <option value="professional">Professional</option>
              <option value="friendly">Friendly</option>
              <option value="formal">Formal</option>
            </select>
          </div>

          <div className="field">
            <label className="label">Response length</label>
            <select className="select" value={form.max_response_length ?? "medium"}
                    onChange={(e) => update("max_response_length", e.target.value)}>
              <option value="short">Short — up to 3 paragraphs</option>
              <option value="medium">Medium</option>
              <option value="long">Long — detailed</option>
            </select>
          </div>

          <div className="field">
            <label className="label">Response language</label>
            <input className="input" value={form.response_language ?? "english"}
                   onChange={(e) => update("response_language", e.target.value)} />
          </div>
        </div>
      </div>

      <div className="card">
        <div className="card-header">
          <div>
            <div className="card-title">Booking &amp; escalation</div>
            <div className="card-desc">Where the bot sends people it can&apos;t help.</div>
          </div>
        </div>
        <div className="card-body">
          <div className="field">
            <label className="label">Booking URL</label>
            <input className="input" value={form.booking_url ?? ""}
                   onChange={(e) => update("booking_url", e.target.value)}
                   placeholder="https://calendly.com/your-team/discovery" />
            <p className="hint">Shared when a visitor is ready to talk to someone.</p>
          </div>

          <div className="field">
            <label className="label">Escalation email</label>
            <input className="input" type="email" value={form.escalation_email ?? ""}
                   onChange={(e) => update("escalation_email", e.target.value)}
                   placeholder="support@yourcompany.com" />
          </div>

          <div className="field">
            <label className="label">Escalation phone</label>
            <input className="input" value={form.escalation_phone ?? ""}
                   onChange={(e) => update("escalation_phone", e.target.value)}
                   placeholder="+1 555 000 1234" />
          </div>

          <div className="field">
            <label className="label">Business hours</label>
            <input className="input" value={form.business_hours ?? ""}
                   onChange={(e) => update("business_hours", e.target.value)}
                   placeholder="Mon–Fri, 9AM–6PM EST" />
          </div>
        </div>
      </div>

      <div className="card">
        <div className="card-header">
          <div>
            <div className="card-title">Danger zone</div>
            <div className="card-desc">Archiving stops the bot from responding.</div>
          </div>
        </div>
        <div className="card-body">
          <p className="hint" style={{ marginBottom: 12 }}>
            Conversation history and captured leads are preserved for auditing.
          </p>
          <button className="btn btn-danger" onClick={onArchive}>
            <IconTrash /> Archive this bot
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Branding ─────────────────────────────────────────────────────────────────

function BrandingTab({
  form, update,
}: {
  form: Partial<Bot>;
  update: <K extends keyof Bot>(key: K, value: Bot[K]) => void;
}) {
  const primary = form.primary_color || "#E6007E";
  const secondary = form.secondary_color || "#1A1B3A";

  return (
    <div className="grid grid-2">
      <div className="card">
        <div className="card-header">
          <div>
            <div className="card-title">Appearance</div>
            <div className="card-desc">How the widget looks on your website.</div>
          </div>
        </div>
        <div className="card-body">
          <div className="field">
            <label className="label">Primary color</label>
            <div className="color-row">
              <input type="color" value={primary}
                     onChange={(e) => update("primary_color", e.target.value)} />
              <input className="input" value={primary}
                     onChange={(e) => update("primary_color", e.target.value)} />
            </div>
            <p className="hint">Launcher button, user message bubbles, send button.</p>
          </div>

          <div className="field">
            <label className="label">Header color</label>
            <div className="color-row">
              <input type="color" value={secondary}
                     onChange={(e) => update("secondary_color", e.target.value)} />
              <input className="input" value={secondary}
                     onChange={(e) => update("secondary_color", e.target.value)} />
            </div>
          </div>

          <div className="field">
            <label className="label">Logo URL</label>
            <input className="input" value={form.logo_url ?? ""}
                   onChange={(e) => update("logo_url", e.target.value)}
                   placeholder="https://yoursite.com/logo.png" />
            <p className="hint">Square image works best. Falls back to an initial.</p>
          </div>

          <div className="field">
            <label className="label">Placement</label>
            <select className="select" value={form.placement ?? "bottom-right"}
                    onChange={(e) => update("placement", e.target.value)}>
              <option value="bottom-right">Bottom right</option>
              <option value="bottom-left">Bottom left</option>
            </select>
          </div>

          <div className="field">
            <label className="label">Launcher label</label>
            <input className="input" value={form.launcher_label ?? ""}
                   onChange={(e) => update("launcher_label", e.target.value)}
                   placeholder="Chat with us" />
            <p className="hint">Leave empty for an icon-only round button.</p>
          </div>
        </div>
      </div>

      <div className="card">
        <div className="card-header">
          <div>
            <div className="card-title">Preview</div>
            <div className="card-desc">Approximate — colors and layout.</div>
          </div>
        </div>
        <div className="card-body">
          <div style={{
            border: "1px solid var(--border)", borderRadius: 12,
            overflow: "hidden", maxWidth: 320,
          }}>
            <div style={{
              background: secondary, color: "#fff", padding: 14,
              display: "flex", alignItems: "center", gap: 10,
            }}>
              <div style={{
                width: 32, height: 32, borderRadius: "50%", background: primary,
                display: "flex", alignItems: "center", justifyContent: "center",
                fontWeight: 700, fontSize: 14, overflow: "hidden",
              }}>
                {form.logo_url
                  ? <img src={form.logo_url} alt=""
                         style={{ width: "100%", height: "100%", objectFit: "cover" }} />
                  : (form.name || "A").charAt(0).toUpperCase()}
              </div>
              <div>
                <div style={{ fontSize: 14, fontWeight: 600 }}>
                  {form.name || "Your business"}
                </div>
                <div style={{ fontSize: 11, opacity: 0.75 }}>Online</div>
              </div>
            </div>

            <div style={{
              background: "#F7F8FA", padding: 14,
              display: "flex", flexDirection: "column", gap: 9, minHeight: 150,
            }}>
              <div style={{
                background: "#fff", border: "1px solid #E5E7EB",
                borderRadius: 12, borderBottomLeftRadius: 4,
                padding: "9px 12px", fontSize: 13, alignSelf: "flex-start",
                maxWidth: "85%",
              }}>
                {form.welcome_message || "Hi! How can I help you today?"}
              </div>
              <div style={{
                background: primary, color: "#fff",
                borderRadius: 12, borderBottomRightRadius: 4,
                padding: "9px 12px", fontSize: 13, alignSelf: "flex-end",
                maxWidth: "85%",
              }}>
                What services do you offer?
              </div>
            </div>

            <div style={{
              padding: 10, background: "#fff", borderTop: "1px solid #E5E7EB",
              display: "flex", gap: 8, alignItems: "center",
            }}>
              <div style={{
                flex: 1, border: "1px solid #D1D5DB", borderRadius: 10,
                padding: "8px 11px", fontSize: 13, color: "#9CA3AF",
              }}>
                Type your message…
              </div>
              <div style={{
                width: 36, height: 36, borderRadius: 10, background: primary,
                display: "flex", alignItems: "center", justifyContent: "center",
                color: "#fff", fontSize: 15,
              }}>→</div>
            </div>
          </div>

          <div style={{ marginTop: 16 }}>
            <div className="label">Launcher</div>
            <div style={{
              display: "inline-flex", alignItems: "center", gap: 8,
              height: 48, padding: form.launcher_label ? "0 18px" : 0,
              width: form.launcher_label ? "auto" : 48,
              justifyContent: "center",
              borderRadius: 24, background: primary, color: "#fff",
              fontSize: 14, fontWeight: 600,
              boxShadow: "0 4px 14px rgba(0,0,0,0.2)",
            }}>
              {form.launcher_label || "💬"}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

// ── Domains ──────────────────────────────────────────────────────────────────

function DomainsTab({
  botId, domains, setDomains,
}: {
  botId: string;
  domains: BotDomain[];
  setDomains: (domains: BotDomain[]) => void;
}) {
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function add(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const created = await api.addDomain(botId, value.trim());
      setDomains([...domains, created]);
      setValue("");
    } catch (e) {
      setError((e as ApiError).message);
    } finally {
      setBusy(false);
    }
  }

  async function remove(domainId: string) {
    try {
      await api.removeDomain(botId, domainId);
      setDomains(domains.filter((d) => d.id !== domainId));
    } catch (e) {
      setError((e as ApiError).message);
    }
  }

  return (
    <div className="card" style={{ maxWidth: 720 }}>
      <div className="card-header">
        <div>
          <div className="card-title">Allowed domains</div>
          <div className="card-desc">
            Restricts which websites may embed this bot.
          </div>
        </div>
      </div>
      <div className="card-body">
        {error && <Alert kind="error">{error}</Alert>}

        {domains.length === 0 && (
          <Alert kind="warning">
            No domains registered — this bot currently answers requests from{" "}
            <strong>any</strong> website. Add your domain before going live.
          </Alert>
        )}

        <form onSubmit={add} style={{ display: "flex", gap: 8, marginBottom: 18 }}>
          <input
            className="input"
            value={value}
            onChange={(e) => setValue(e.target.value)}
            placeholder="example.com or *.example.com"
            required
          />
          <button className="btn btn-primary" disabled={busy || !value.trim()}>
            {busy ? <Spinner /> : <><IconPlus /> Add</>}
          </button>
        </form>

        {domains.length > 0 && (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Domain</th><th>Added</th><th></th></tr></thead>
              <tbody>
                {domains.map((domain) => (
                  <tr key={domain.id}>
                    <td>
                      <span style={{
                        display: "inline-flex", alignItems: "center", gap: 7,
                      }}>
                        <IconGlobe /> <span className="cell-strong">{domain.domain}</span>
                      </span>
                    </td>
                    <td className="cell-muted">{formatDate(domain.created_at)}</td>
                    <td style={{ textAlign: "right" }}>
                      <button className="btn btn-ghost btn-sm"
                              onClick={() => remove(domain.id)}
                              aria-label={`Remove ${domain.domain}`}>
                        <IconTrash />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        <p className="hint" style={{ marginTop: 14 }}>
          <code>*.example.com</code> matches every subdomain and the apex domain.
          Requests from <code>localhost</code> are always allowed for development.
        </p>
      </div>
    </div>
  );
}
