"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Shell } from "@/components/Shell";
import { Alert, LoadingState, EmptyState } from "@/components/ui";
import { IconCode, IconPlus } from "@/components/Icons";
import { EmbedSnippet } from "@/components/EmbedSnippet";
import { api, ApiError } from "@/lib/api";
import type { Bot } from "@/lib/types";

export default function EmbedPage() {
  const [bots, setBots] = useState<Bot[]>([]);
  const [selected, setSelected] = useState<string>("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.listBots()
      .then((data) => {
        const active = data.filter((b) => b.status !== "archived");
        setBots(active);
        if (active.length > 0) setSelected(active[0].id);
      })
      .catch((e: ApiError) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  const bot = bots.find((b) => b.id === selected);

  return (
    <Shell
      title="Embed code"
      subtitle="One script tag puts your assistant on any website."
    >
      {error && <Alert kind="error">{error}</Alert>}

      {loading ? (
        <LoadingState />
      ) : bots.length === 0 ? (
        <div className="card">
          <EmptyState
            icon={<IconCode />}
            title="No bots to embed"
            text="Create a bot first, then come back for its embed snippet."
            action={
              <Link href="/bots" className="btn btn-primary">
                <IconPlus /> Create a bot
              </Link>
            }
          />
        </div>
      ) : (
        <>
          {bots.length > 1 && (
            <div className="card" style={{ marginBottom: 16, maxWidth: 780 }}>
              <div className="card-body" style={{ paddingBottom: 16 }}>
                <div className="field" style={{ marginBottom: 0 }}>
                  <label className="label">Bot</label>
                  <select
                    className="select"
                    value={selected}
                    onChange={(e) => setSelected(e.target.value)}
                  >
                    {bots.map((b) => (
                      <option key={b.id} value={b.id}>{b.name}</option>
                    ))}
                  </select>
                </div>
              </div>
            </div>
          )}

          {bot && <EmbedSnippet botId={bot.id} botName={bot.name} />}

          <div className="card" style={{ marginTop: 16, maxWidth: 780 }}>
            <div className="card-header">
              <div>
                <div className="card-title">Before you go live</div>
                <div className="card-desc">
                  A short checklist for a new deployment.
                </div>
              </div>
            </div>
            <div className="card-body">
              <ol style={{
                margin: 0, paddingLeft: 20, fontSize: 13.5, lineHeight: 2,
              }}>
                <li>
                  Add your website to the{" "}
                  <Link href={bot ? `/bots/${bot.id}` : "/bots"}>Domains tab</Link>{" "}
                  so no one else can embed this bot.
                </li>
                <li>
                  Upload your FAQs and service content on the{" "}
                  <Link href="/knowledge">Knowledge</Link> page — the bot declines
                  anything it can&apos;t ground in that content.
                </li>
                <li>Set a booking URL so qualified visitors can schedule a call.</li>
                <li>Ask the bot a few real questions and confirm the answers are correct.</li>
                <li>Paste the snippet into your site and reload the page.</li>
              </ol>
            </div>
          </div>
        </>
      )}
    </Shell>
  );
}
