"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Shell } from "@/components/Shell";
import { Alert, LoadingState, formatRelative, StatusBadge } from "@/components/ui";
import { api, ApiError } from "@/lib/api";
import type { DashboardStats, Conversation, Lead } from "@/lib/types";

function Stat({
  label, value, hint,
}: { label: string; value: string | number; hint?: string }) {
  return (
    <div className="card stat">
      <div className="stat-label">{label}</div>
      <div className="stat-value">{value}</div>
      {hint && <div className="stat-hint">{hint}</div>}
    </div>
  );
}

export default function OverviewPage() {
  const [stats, setStats] = useState<DashboardStats | null>(null);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [leads, setLeads] = useState<Lead[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all([
      api.stats(),
      api.listConversations(6),
      api.listLeads(),
    ])
      .then(([statsData, conversationsData, leadsData]) => {
        setStats(statsData);
        setConversations(conversationsData);
        setLeads(leadsData.slice(0, 6));
      })
      .catch((e: ApiError) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  const modelEntries = Object.entries(stats?.model_distribution ?? {});
  const totalModelCalls = modelEntries.reduce((sum, [, count]) => sum + count, 0);

  return (
    <Shell
      title="Overview"
      subtitle="Activity across every bot in your workspace."
    >
      {error && <Alert kind="error">{error}</Alert>}

      {loading ? (
        <LoadingState label="Loading your dashboard…" />
      ) : (
        <>
          <div className="grid grid-4" style={{ marginBottom: 16 }}>
            <Stat label="Conversations" value={stats?.total_conversations ?? 0} />
            <Stat label="Leads captured" value={stats?.total_leads ?? 0} />
            <Stat
              label="Pending handoffs"
              value={stats?.pending_handoffs ?? 0}
              hint={stats?.pending_handoffs ? "Needs a human" : "All clear"}
            />
            <Stat
              label="Knowledge chunks"
              value={stats?.total_chunks ?? 0}
              hint={
                (stats?.total_chunks ?? 0) === 0
                  ? "Add content to ground answers"
                  : undefined
              }
            />
          </div>

          <div className="grid grid-3" style={{ marginBottom: 16 }}>
            <Stat
              label="Model cost"
              value={`$${(stats?.total_cost_usd ?? 0).toFixed(4)}`}
              hint="Last 200 requests"
            />
            <Stat
              label="Avg latency"
              value={`${stats?.avg_latency_ms ?? 0} ms`}
              hint="Time to full response"
            />
            <Stat label="Active bots" value={stats?.total_bots ?? 0} />
          </div>

          {(stats?.total_chunks ?? 0) === 0 && (
            <Alert kind="warning">
              Your knowledge base is empty, so the bot will decline most questions.{" "}
              <Link href="/knowledge">Add knowledge</Link> to ground its answers.
            </Alert>
          )}

          <div className="grid grid-2">
            <div className="card">
              <div className="card-header">
                <div>
                  <div className="card-title">Recent conversations</div>
                  <div className="card-desc">Latest visitor sessions</div>
                </div>
                <Link href="/conversations" className="btn btn-secondary btn-sm">
                  View all
                </Link>
              </div>
              {conversations.length === 0 ? (
                <div className="card-body">
                  <p className="cell-muted">No conversations yet.</p>
                </div>
              ) : (
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr><th>Session</th><th>Status</th><th>Started</th></tr>
                    </thead>
                    <tbody>
                      {conversations.map((conversation) => (
                        <tr key={conversation.id}>
                          <td>
                            <Link href={`/conversations/${conversation.id}`}
                                  className="cell-strong">
                              {conversation.session_id?.slice(0, 16) ?? "—"}…
                            </Link>
                          </td>
                          <td><StatusBadge status={conversation.status} /></td>
                          <td className="cell-muted">
                            {formatRelative(conversation.created_at)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>

            <div className="card">
              <div className="card-header">
                <div>
                  <div className="card-title">Recent leads</div>
                  <div className="card-desc">Captured by the sales flow</div>
                </div>
                <Link href="/leads" className="btn btn-secondary btn-sm">View all</Link>
              </div>
              {leads.length === 0 ? (
                <div className="card-body">
                  <p className="cell-muted">No leads captured yet.</p>
                </div>
              ) : (
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr><th>Name</th><th>Stage</th><th>Captured</th></tr>
                    </thead>
                    <tbody>
                      {leads.map((lead) => (
                        <tr key={lead.id}>
                          <td>
                            <div className="cell-strong">{lead.name || "Unnamed"}</div>
                            <div className="cell-muted" style={{ fontSize: 12 }}>
                              {lead.email || "—"}
                            </div>
                          </td>
                          <td><StatusBadge status={lead.lead_stage} /></td>
                          <td className="cell-muted">
                            {formatRelative(lead.created_at)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          </div>

          {totalModelCalls > 0 && (
            <div className="card" style={{ marginTop: 16 }}>
              <div className="card-header">
                <div>
                  <div className="card-title">Model routing</div>
                  <div className="card-desc">
                    Luna handles routine traffic; Terra is the selective fallback.
                  </div>
                </div>
              </div>
              <div className="card-body">
                {modelEntries.map(([model, count]) => {
                  const percent = Math.round((count / totalModelCalls) * 100);
                  return (
                    <div key={model} style={{ marginBottom: 12 }}>
                      <div style={{
                        display: "flex", justifyContent: "space-between",
                        fontSize: 13, marginBottom: 4,
                      }}>
                        <span className="cell-strong">{model}</span>
                        <span className="cell-muted">{count} calls ({percent}%)</span>
                      </div>
                      <div style={{
                        height: 7, background: "var(--bg)",
                        borderRadius: 4, overflow: "hidden",
                      }}>
                        <div style={{
                          width: `${percent}%`, height: "100%",
                          background: "var(--brand)", borderRadius: 4,
                        }} />
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}
        </>
      )}
    </Shell>
  );
}
