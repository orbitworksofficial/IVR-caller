"use client";

import { useEffect, useState } from "react";
import { Shell } from "@/components/Shell";
import {
  Alert, LoadingState, EmptyState, StatusBadge, formatRelative,
} from "@/components/ui";
import { IconHandoff, IconCheck } from "@/components/Icons";
import { api, ApiError } from "@/lib/api";
import type { Handoff } from "@/lib/types";

export default function HandoffsPage() {
  const [handoffs, setHandoffs] = useState<Handoff[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [updating, setUpdating] = useState<string | null>(null);

  useEffect(() => {
    api.listHandoffs()
      .then(setHandoffs)
      .catch((e: ApiError) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  async function setStatus(id: string, status: string) {
    setUpdating(id);
    try {
      const updated = await api.updateHandoff(id, status);
      setHandoffs(handoffs.map((h) => (h.id === id ? updated : h)));
    } catch (e) {
      setError((e as ApiError).message);
    } finally {
      setUpdating(null);
    }
  }

  const pending = handoffs.filter((h) => h.status === "pending");

  return (
    <Shell
      title="Handoffs"
      subtitle="Conversations the bot escalated to a human."
    >
      {error && <Alert kind="error">{error}</Alert>}

      {loading ? (
        <LoadingState label="Loading handoffs…" />
      ) : handoffs.length === 0 ? (
        <div className="card">
          <EmptyState
            icon={<IconHandoff />}
            title="No handoffs"
            text="When a visitor gets frustrated, asks for a human, or asks something outside the knowledge base, the request appears here."
          />
        </div>
      ) : (
        <>
          {pending.length > 0 && (
            <Alert kind="warning">
              <strong>{pending.length}</strong>{" "}
              {pending.length === 1 ? "request needs" : "requests need"} a response.
            </Alert>
          )}

          <div className="card">
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Reason</th><th>Context</th>
                    <th>Status</th><th>Requested</th><th></th>
                  </tr>
                </thead>
                <tbody>
                  {handoffs.map((handoff) => (
                    <tr key={handoff.id}>
                      <td className="cell-strong" style={{ maxWidth: 220 }}>
                        {handoff.reason || "—"}
                      </td>
                      <td className="cell-muted" style={{ maxWidth: 380 }}>
                        {handoff.summary || "—"}
                      </td>
                      <td><StatusBadge status={handoff.status} /></td>
                      <td className="cell-muted">
                        {formatRelative(handoff.created_at)}
                      </td>
                      <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                        {handoff.status === "pending" && (
                          <button
                            className="btn btn-secondary btn-sm"
                            onClick={() => setStatus(handoff.id, "in_progress")}
                            disabled={updating === handoff.id}
                          >
                            Claim
                          </button>
                        )}
                        {handoff.status !== "resolved" && (
                          <button
                            className="btn btn-primary btn-sm"
                            style={{ marginLeft: 6 }}
                            onClick={() => setStatus(handoff.id, "resolved")}
                            disabled={updating === handoff.id}
                          >
                            <IconCheck /> Resolve
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}
    </Shell>
  );
}
