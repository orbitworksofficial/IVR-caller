"use client";

import { useEffect, useState } from "react";
import { Shell } from "@/components/Shell";
import {
  Alert, LoadingState, EmptyState, Modal, Spinner, formatDate,
} from "@/components/ui";
import {
  IconGlobe, IconPlus, IconTrash, IconCopy, IconCheck,
} from "@/components/Icons";
import { api, ApiError, setActiveTenant } from "@/lib/api";
import type { TenantSummary, AdminAccount, CreateTenantResult, Admin } from "@/lib/types";

export default function TenantsPage() {
  const [tenants, setTenants] = useState<TenantSummary[]>([]);
  const [admins, setAdmins] = useState<AdminAccount[]>([]);
  const [me, setMe] = useState<Admin | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [created, setCreated] = useState<CreateTenantResult | null>(null);
  const [tab, setTab] = useState<"tenants" | "accounts">("tenants");

  function load() {
    setLoading(true);
    Promise.all([api.listTenants(), api.listAdmins(), api.me()])
      .then(([t, a, m]) => { setTenants(t); setAdmins(a); setMe(m); })
      .catch((e: ApiError) => setError(e.message))
      .finally(() => setLoading(false));
  }

  useEffect(load, []);

  function openTenant(tenantId: string) {
    if (!me) return;
    setActiveTenant(tenantId === me.own_tenant_id ? null : tenantId);
    window.location.href = "/";
  }

  async function removeTenant(t: TenantSummary) {
    const typed = prompt(
      `This permanently deletes "${t.name}" and ALL of its data — ` +
      `${t.conversation_count} conversations, ${t.lead_count} leads, ` +
      `${t.chunk_count} knowledge chunks. This cannot be undone.\n\n` +
      `Type the business name to confirm:`
    );
    if (typed !== t.name) {
      if (typed !== null) setError("Name did not match — nothing was deleted.");
      return;
    }
    try {
      await api.deleteTenant(t.id);
      setNotice(`Deleted "${t.name}".`);
      load();
    } catch (e) {
      setError((e as ApiError).message);
    }
  }

  async function changeRole(account: AdminAccount, role: string) {
    try {
      await api.updateAdmin(account.id, { role });
      setNotice(`${account.email} is now a ${role}.`);
      load();
    } catch (e) {
      setError((e as ApiError).message);
    }
  }

  const totals = tenants.reduce(
    (acc, t) => ({
      conversations: acc.conversations + t.conversation_count,
      leads: acc.leads + t.lead_count,
      handoffs: acc.handoffs + t.pending_handoffs,
    }),
    { conversations: 0, leads: 0, handoffs: 0 }
  );

  return (
    <Shell
      title="All tenants"
      subtitle="Every client on the platform. Only super-admins can see this."
      actions={
        <button className="btn btn-primary" onClick={() => setShowCreate(true)}>
          <IconPlus /> New client
        </button>
      }
    >
      {error && <Alert kind="error">{error}</Alert>}
      {notice && <Alert kind="success">{notice}</Alert>}

      {loading ? (
        <LoadingState label="Loading tenants…" />
      ) : (
        <>
          <div className="grid grid-4" style={{ marginBottom: 16 }}>
            <div className="card stat">
              <div className="stat-label">Tenants</div>
              <div className="stat-value">{tenants.length}</div>
            </div>
            <div className="card stat">
              <div className="stat-label">Conversations</div>
              <div className="stat-value">{totals.conversations}</div>
              <div className="stat-hint">across all clients</div>
            </div>
            <div className="card stat">
              <div className="stat-label">Leads</div>
              <div className="stat-value">{totals.leads}</div>
            </div>
            <div className="card stat">
              <div className="stat-label">Pending handoffs</div>
              <div className="stat-value">{totals.handoffs}</div>
              <div className="stat-hint">
                {totals.handoffs > 0 ? "need a human" : "all clear"}
              </div>
            </div>
          </div>

          <div className="tabs">
            <button className={`tab${tab === "tenants" ? " active" : ""}`}
                    onClick={() => setTab("tenants")}>
              Tenants ({tenants.length})
            </button>
            <button className={`tab${tab === "accounts" ? " active" : ""}`}
                    onClick={() => setTab("accounts")}>
              Accounts ({admins.length})
            </button>
          </div>

          {tab === "tenants" && (
            tenants.length === 0 ? (
              <div className="card">
                <EmptyState
                  icon={<IconGlobe />}
                  title="No tenants yet"
                  text="Create your first client to get started."
                  action={
                    <button className="btn btn-primary" onClick={() => setShowCreate(true)}>
                      <IconPlus /> New client
                    </button>
                  }
                />
              </div>
            ) : (
              <div className="card">
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>Business</th><th>Bots</th><th>Knowledge</th>
                        <th>Chats</th><th>Leads</th><th>Handoffs</th>
                        <th>Created</th><th></th>
                      </tr>
                    </thead>
                    <tbody>
                      {tenants.map((t) => (
                        <tr key={t.id}>
                          <td>
                            <div className="cell-strong">{t.name}</div>
                            <div className="cell-muted" style={{ fontSize: 12 }}>
                              {t.contact_email || t.industry || "—"}
                            </div>
                          </td>
                          <td className="cell-muted">{t.bot_count}</td>
                          <td className="cell-muted">
                            {t.chunk_count === 0
                              ? <span className="badge badge-warning">empty</span>
                              : t.chunk_count}
                          </td>
                          <td className="cell-muted">{t.conversation_count}</td>
                          <td className="cell-muted">{t.lead_count}</td>
                          <td>
                            {t.pending_handoffs > 0
                              ? <span className="badge badge-danger">{t.pending_handoffs}</span>
                              : <span className="cell-muted">0</span>}
                          </td>
                          <td className="cell-muted">{formatDate(t.created_at)}</td>
                          <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                            <button className="btn btn-secondary btn-sm"
                                    onClick={() => openTenant(t.id)}>
                              Open
                            </button>
                            {me && t.id !== me.own_tenant_id && (
                              <button className="btn btn-ghost btn-sm"
                                      style={{ marginLeft: 6 }}
                                      onClick={() => removeTenant(t)}
                                      aria-label={`Delete ${t.name}`}>
                                <IconTrash />
                              </button>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )
          )}

          {tab === "accounts" && (
            <div className="card">
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Email</th><th>Tenant</th><th>Role</th>
                      <th>Created</th><th></th>
                    </tr>
                  </thead>
                  <tbody>
                    {admins.map((a) => (
                      <tr key={a.id}>
                        <td>
                          <div className="cell-strong">{a.email || "(unknown)"}</div>
                          {a.is_super_admin && (
                            <span className="badge badge-brand">Super admin</span>
                          )}
                        </td>
                        <td className="cell-muted">{a.tenant_name || "—"}</td>
                        <td>
                          <select
                            className="select"
                            value={a.role}
                            disabled={a.is_super_admin}
                            onChange={(e) => changeRole(a, e.target.value)}
                            style={{ width: "auto", padding: "4px 8px", fontSize: 12.5 }}
                          >
                            <option value="client">Client — view + knowledge</option>
                            <option value="admin">Admin — full control</option>
                          </select>
                        </td>
                        <td className="cell-muted">{formatDate(a.created_at)}</td>
                        <td style={{ textAlign: "right" }}>
                          {me && a.user_id !== me.user_id && (
                            <button
                              className="btn btn-ghost btn-sm"
                              onClick={async () => {
                                if (!confirm(`Revoke access for ${a.email}?`)) return;
                                try {
                                  await api.deleteAdmin(a.id);
                                  setNotice("Access revoked.");
                                  load();
                                } catch (e) { setError((e as ApiError).message); }
                              }}
                              aria-label="Revoke access"
                            >
                              <IconTrash />
                            </button>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="card-body" style={{ paddingTop: 0 }}>
                <p className="hint">
                  <strong>Client</strong> accounts can view leads, conversations and
                  handoffs, and manage their own knowledge base. They cannot change
                  bot settings, branding or domains. <strong>Admin</strong> accounts
                  have full control of their tenant.
                </p>
              </div>
            </div>
          )}
        </>
      )}

      {showCreate && (
        <CreateTenantModal
          onClose={() => setShowCreate(false)}
          onCreated={(result) => {
            setShowCreate(false);
            setCreated(result);
            load();
          }}
        />
      )}

      {created && (
        <CredentialsModal result={created} onClose={() => setCreated(null)} />
      )}
    </Shell>
  );
}

// ── Create ────────────────────────────────────────────────────────────────────

function CreateTenantModal({
  onClose, onCreated,
}: {
  onClose: () => void;
  onCreated: (r: CreateTenantResult) => void;
}) {
  const [businessName, setBusinessName] = useState("");
  const [adminEmail, setAdminEmail] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState("client");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function generatePassword() {
    // Ambiguous characters removed so the password survives being read aloud
    // or copied from an email.
    const chars = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789";
    const bytes = new Uint32Array(16);
    crypto.getRandomValues(bytes);
    setPassword(Array.from(bytes, (b) => chars[b % chars.length]).join(""));
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const result = await api.createTenant({
        business_name: businessName.trim(),
        admin_email: adminEmail.trim(),
        admin_password: password,
        role,
      });
      onCreated(result);
    } catch (err) {
      setError((err as ApiError).message);
      setBusy(false);
    }
  }

  const valid = businessName.trim() && adminEmail.trim().includes("@") && password.length >= 8;

  return (
    <Modal
      title="New client"
      onClose={onClose}
      footer={
        <>
          <button className="btn btn-secondary" onClick={onClose} disabled={busy}>
            Cancel
          </button>
          <button className="btn btn-primary" onClick={submit} disabled={busy || !valid}>
            {busy ? <><Spinner /> Creating…</> : "Create client"}
          </button>
        </>
      }
    >
      {error && <Alert kind="error">{error}</Alert>}

      <form onSubmit={submit}>
        <div className="field">
          <label className="label">Business name</label>
          <input className="input" value={businessName} autoFocus required
                 onChange={(e) => setBusinessName(e.target.value)}
                 placeholder="Acme Corp" />
          <p className="hint">Creates the tenant and their first bot.</p>
        </div>

        <div className="field">
          <label className="label">Login email</label>
          <input className="input" type="email" value={adminEmail} required
                 onChange={(e) => setAdminEmail(e.target.value)}
                 placeholder="contact@acme.com" />
          <p className="hint">They sign in with this. It is not sent an email.</p>
        </div>

        <div className="field">
          <label className="label">Password</label>
          <div style={{ display: "flex", gap: 8 }}>
            <input className="input" value={password} required minLength={8}
                   onChange={(e) => setPassword(e.target.value)}
                   placeholder="At least 8 characters" />
            <button type="button" className="btn btn-secondary" onClick={generatePassword}>
              Generate
            </button>
          </div>
          <p className="hint">
            You will see this once on the next screen — copy it before closing.
          </p>
        </div>

        <div className="field">
          <label className="label">Account type</label>
          <select className="select" value={role} onChange={(e) => setRole(e.target.value)}>
            <option value="client">Client — view data, manage knowledge</option>
            <option value="admin">Admin — full control of their tenant</option>
          </select>
        </div>
      </form>
    </Modal>
  );
}

// ── Credentials shown once ────────────────────────────────────────────────────

function CredentialsModal({
  result, onClose,
}: {
  result: CreateTenantResult;
  onClose: () => void;
}) {
  const [copied, setCopied] = useState<string | null>(null);

  function copy(text: string, label: string) {
    navigator.clipboard.writeText(text)
      .then(() => { setCopied(label); setTimeout(() => setCopied(null), 2000); })
      .catch(() => {});
  }

  return (
    <Modal
      title="Client created"
      onClose={onClose}
      footer={<button className="btn btn-primary" onClick={onClose}>Done</button>}
    >
      <Alert kind="warning">
        Copy these now — the password is not stored anywhere and cannot be shown again.
      </Alert>

      <div className="field">
        <label className="label">Dashboard</label>
        <div className="code-block" style={{ fontSize: 12 }}>
          https://admin.orb-itworks.com
        </div>
      </div>

      <div className="field">
        <label className="label">Email</label>
        <div style={{ display: "flex", gap: 8 }}>
          <input className="input" readOnly value={result.admin_email} />
          <button className="btn btn-secondary"
                  onClick={() => copy(result.admin_email, "email")}>
            {copied === "email" ? <IconCheck /> : <IconCopy />}
          </button>
        </div>
      </div>

      <div className="field">
        <label className="label">Embed snippet for their website</label>
        <pre className="code-block">{result.embed_snippet}</pre>
        <button className="btn btn-secondary btn-sm" style={{ marginTop: 8 }}
                onClick={() => copy(result.embed_snippet, "snippet")}>
          {copied === "snippet" ? <><IconCheck /> Copied</> : <><IconCopy /> Copy snippet</>}
        </button>
      </div>

      <Alert kind="info">
        Their bot has no knowledge yet, so it will decline every question. Switch
        into their tenant and add content under <strong>Knowledge</strong> before
        they go live.
      </Alert>
    </Modal>
  );
}
