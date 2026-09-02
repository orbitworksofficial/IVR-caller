"use client";

import { useEffect, useState, use } from "react";
import Link from "next/link";
import { Shell } from "@/components/Shell";
import { Alert, LoadingState, StatusBadge, formatDate } from "@/components/ui";
import { IconBack } from "@/components/Icons";
import { api, ApiError } from "@/lib/api";
import type { ConversationDetail } from "@/lib/types";

export default function ConversationDetailPage({
  params,
}: {
  params: Promise<{ conversationId: string }>;
}) {
  const { conversationId } = use(params);
  const [conversation, setConversation] = useState<ConversationDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.getConversation(conversationId)
      .then(setConversation)
      .catch((e: ApiError) => setError(e.message))
      .finally(() => setLoading(false));
  }, [conversationId]);

  if (loading) {
    return <Shell title="Conversation"><LoadingState /></Shell>;
  }

  if (!conversation) {
    return (
      <Shell title="Conversation">
        <Alert kind="error">{error || "Conversation not found."}</Alert>
        <Link href="/conversations" className="btn btn-secondary">
          <IconBack /> Back
        </Link>
      </Shell>
    );
  }

  return (
    <Shell
      title="Conversation"
      subtitle={conversation.session_id ? `Session ${conversation.session_id}` : undefined}
      actions={
        <Link href="/conversations" className="btn btn-secondary">
          <IconBack /> Back
        </Link>
      }
    >
      <div className="grid" style={{ gridTemplateColumns: "1fr 300px", gap: 16 }}>
        <div className="card">
          <div className="card-header">
            <div>
              <div className="card-title">Transcript</div>
              <div className="card-desc">
                {conversation.messages.length} messages
              </div>
            </div>
          </div>
          <div className="card-body">
            {conversation.messages.length === 0 ? (
              <p className="cell-muted">No messages in this conversation.</p>
            ) : (
              <div className="transcript">
                {conversation.messages.map((message, index) => (
                  <div
                    key={message.id ?? index}
                    style={{
                      display: "flex", flexDirection: "column",
                      alignItems: message.role === "user" ? "flex-end" : "flex-start",
                    }}
                  >
                    <div className={`bubble ${message.role === "user" ? "user" : "assistant"}`}>
                      {message.content}
                    </div>
                    <div className="bubble-meta">
                      {message.role === "user" ? "Visitor" : "Assistant"} ·{" "}
                      {formatDate(message.created_at)}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>

        <div>
          <div className="card" style={{ marginBottom: 16 }}>
            <div className="card-header">
              <div className="card-title">Details</div>
            </div>
            <div className="card-body">
              <div style={{ marginBottom: 12 }}>
                <div className="stat-label">Status</div>
                <div style={{ marginTop: 4 }}>
                  <StatusBadge status={conversation.status} />
                </div>
              </div>
              <div style={{ marginBottom: 12 }}>
                <div className="stat-label">Session</div>
                <div className="cell-mono" style={{ marginTop: 4, wordBreak: "break-all" }}>
                  {conversation.session_id || "—"}
                </div>
              </div>
              <div>
                <div className="stat-label">Conversation ID</div>
                <div className="cell-mono" style={{ marginTop: 4, wordBreak: "break-all" }}>
                  {conversation.id}
                </div>
              </div>
            </div>
          </div>

          {conversation.summary && (
            <div className="card">
              <div className="card-header">
                <div>
                  <div className="card-title">Memory summary</div>
                  <div className="card-desc">
                    Compacted context for longer chats
                  </div>
                </div>
              </div>
              <div className="card-body">
                <p style={{ fontSize: 13, color: "var(--text-muted)", lineHeight: 1.6 }}>
                  {conversation.summary}
                </p>
              </div>
            </div>
          )}
        </div>
      </div>
    </Shell>
  );
}
