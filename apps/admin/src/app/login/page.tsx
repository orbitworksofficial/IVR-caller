"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { createClient } from "@/lib/supabase";
import { Alert, Spinner } from "@/components/ui";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    setBusy(true);

    const supabase = createClient();
    const { error: signInError } = await supabase.auth.signInWithPassword({
      email: email.trim(),
      password,
    });

    if (signInError) {
      setError(signInError.message);
      setBusy(false);
      return;
    }

    // refresh() re-runs middleware so the new session cookie is applied before
    // the dashboard renders.
    router.replace("/");
    router.refresh();
  }

  return (
    <div className="auth-page">
      <div className="auth-card">
        <div className="auth-brand">
          <div className="sidebar-logo">O</div>
          <div>
            <div style={{ fontWeight: 600, fontSize: 15 }}>OrbitWorks</div>
            <div style={{ fontSize: 12, color: "var(--text-muted)" }}>
              Chatbot Admin
            </div>
          </div>
        </div>

        <h1 style={{ fontSize: 20, marginBottom: 6 }}>Sign in</h1>
        <p className="page-subtitle" style={{ marginBottom: 20 }}>
          Manage your bots, knowledge base and leads.
        </p>

        {error && <Alert kind="error">{error}</Alert>}

        <form onSubmit={handleSubmit}>
          <div className="field">
            <label className="label" htmlFor="email">Email</label>
            <input
              id="email"
              className="input"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="you@company.com"
              required
              autoComplete="email"
            />
          </div>

          <div className="field">
            <label className="label" htmlFor="password">Password</label>
            <input
              id="password"
              className="input"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="••••••••"
              required
              autoComplete="current-password"
            />
          </div>

          <button
            className="btn btn-primary"
            type="submit"
            disabled={busy}
            style={{ width: "100%", marginTop: 4 }}
          >
            {busy ? <><Spinner /> Signing in…</> : "Sign in"}
          </button>
        </form>

        <p className="hint" style={{ marginTop: 18, textAlign: "center" }}>
          Accounts are created in Supabase Auth and linked to a tenant through
          the <code>tenant_admins</code> table.
        </p>
      </div>
    </div>
  );
}
