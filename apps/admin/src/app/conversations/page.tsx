"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Shell } from "@/components/Shell";
import {
  Alert, LoadingState, EmptyState, StatusBadge, formatRelative,
} from "@/components/ui";
import { IconChat } from "@/components/Icons";
import { api, ApiError } from "@/lib/api";
import type { Conversation } from "@/lib/types";

export default function ConversationsPage() {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.listConversations(100)
      .then(setConversations)
      .catch((e: ApiError) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  return (
    <Shell
      title="Conversations"
      subtitle="Every visitor session, with the full transcript."
    >
      {error && <Alert kind="error">{error}</Alert>}

      {loading ? (
        <LoadingState label="Loading conversations…" />
      ) : conversations.length === 0 ? (
        <div className="card">
          <EmptyState
            icon={<IconChat />}
            title="No conversations yet"
            text="Once the widget is live on your website, visitor conversations appear here."
          />
        </div>
      ) : (
        <div className="card">
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Session</th><th>Status</th>
                  <th>Summary</th><th>Started</th><th></th>
                </tr>
              </thead>
              <tbody>
                {conversations.map((conversation) => (
                  <tr key={conversation.id}>
                    <td className="cell-mono">
                      {conversation.session_id?.slice(0, 20) ?? "—"}
                    </td>
                    <td><StatusBadge status={conversation.status} /></td>
                    <td className="cell-muted" style={{ maxWidth: 380 }}>
                      {conversation.summary
                        ? conversation.summary.slice(0, 130) +
                          (conversation.summary.length > 130 ? "…" : "")
                        : "—"}
                    </td>
                    <td className="cell-muted">
                      {formatRelative(conversation.created_at)}
                    </td>
                    <td style={{ textAlign: "right" }}>
                      <Link
                        href={`/conversations/${conversation.id}`}
                        className="btn btn-secondary btn-sm"
                      >
                        View
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </Shell>
  );
}
