"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Shell } from "@/components/Shell";
import {
  Alert, LoadingState, EmptyState, Modal, Spinner,
  StatusBadge, formatDate,
} from "@/components/ui";
import { IconBot, IconPlus } from "@/components/Icons";
import { api, ApiError } from "@/lib/api";
import type { Bot } from "@/lib/types";

export default function BotsPage() {
  const [bots, setBots] = useState<Bot[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);

  const [name, setName] = useState("");
  const [welcome, setWelcome] = useState("");
  const [personality, setPersonality] = useState("");
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);

  function load() {
    setLoading(true);
    api.listBots()
      .then(setBots)
      .catch((e: ApiError) => setError(e.message))
      .finally(() => setLoading(false));
  }

  useEffect(load, []);

  async function handleCreate(event: React.FormEvent) {
    event.preventDefault();
    setCreateError(null);
    setCreating(true);

    try {
      await api.createBot({
        name: name.trim(),
        welcome_message: welcome.trim() || undefined,
        system_personality: personality.trim() || undefined,
      });
      setShowCreate(false);
      setName(""); setWelcome(""); setPersonality("");
      load();
    } catch (e) {
      setCreateError((e as ApiError).message);
    } finally {
      setCreating(false);
    }
  }

  return (
    <Shell
      title="Bots"
      subtitle="Each bot is one deployable assistant with its own branding and behavior."
      actions={
        <button className="btn btn-primary" onClick={() => setShowCreate(true)}>
          <IconPlus /> New bot
        </button>
      }
    >
      {error && <Alert kind="error">{error}</Alert>}

      {loading ? (
        <LoadingState label="Loading bots…" />
      ) : bots.length === 0 ? (
        <div className="card">
          <EmptyState
            icon={<IconBot />}
            title="No bots yet"
            text="Create your first bot, add its knowledge base, then embed it on your site with a single script tag."
            action={
              <button className="btn btn-primary" onClick={() => setShowCreate(true)}>
                <IconPlus /> Create your first bot
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
                  <th>Name</th><th>Status</th><th>Modes</th>
                  <th>Created</th><th></th>
                </tr>
              </thead>
              <tbody>
                {bots.map((bot) => (
                  <tr key={bot.id}>
                    <td>
                      <Link href={`/bots/${bot.id}`} className="cell-strong">
                        {bot.name}
                      </Link>
                      <div className="cell-mono">{bot.id}</div>
                    </td>
                    <td><StatusBadge status={bot.status} /></td>
                    <td>
                      <div style={{ display: "flex", gap: 5, flexWrap: "wrap" }}>
                        {bot.support_mode_enabled && (
                          <span className="badge badge-info">Support</span>
                        )}
                        {bot.sales_mode_enabled && (
                          <span className="badge badge-brand">Sales</span>
                        )}
                      </div>
                    </td>
                    <td className="cell-muted">{formatDate(bot.created_at)}</td>
                    <td style={{ textAlign: "right" }}>
                      <Link href={`/bots/${bot.id}`} className="btn btn-secondary btn-sm">
                        Configure
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {showCreate && (
        <Modal
          title="Create a bot"
          onClose={() => setShowCreate(false)}
          footer={
            <>
              <button className="btn btn-secondary" onClick={() => setShowCreate(false)}>
                Cancel
              </button>
              <button
                className="btn btn-primary"
                onClick={handleCreate}
                disabled={creating || !name.trim()}
              >
                {creating ? <><Spinner /> Creating…</> : "Create bot"}
              </button>
            </>
          }
        >
          {createError && <Alert kind="error">{createError}</Alert>}

          <form onSubmit={handleCreate}>
            <div className="field">
              <label className="label" htmlFor="bot-name">Business name</label>
              <input
                id="bot-name"
                className="input"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Acme Corp"
                required
                autoFocus
              />
              <p className="hint">Shown in the chat header on your website.</p>
            </div>

            <div className="field">
              <label className="label" htmlFor="bot-welcome">Welcome message</label>
              <input
                id="bot-welcome"
                className="input"
                value={welcome}
                onChange={(e) => setWelcome(e.target.value)}
                placeholder="Hi! How can I help you today?"
              />
            </div>

            <div className="field">
              <label className="label" htmlFor="bot-personality">Business context</label>
              <textarea
                id="bot-personality"
                className="textarea"
                value={personality}
                onChange={(e) => setPersonality(e.target.value)}
                placeholder="You are the assistant for Acme Corp, a B2B logistics company serving mid-market retailers."
              />
              <p className="hint">
                Describes who the bot works for. Facts still come only from the
                knowledge base.
              </p>
            </div>
          </form>
        </Modal>
      )}
    </Shell>
  );
}
