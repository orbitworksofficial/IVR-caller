"use client";

import { ReactNode } from "react";
import { IconClose } from "./Icons";

export function Spinner({ large = false }: { large?: boolean }) {
  return <span className={`spinner${large ? " spinner-lg" : ""}`} aria-label="Loading" />;
}

export function LoadingState({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="loading-center">
      <Spinner large />
      <span>{label}</span>
    </div>
  );
}

export function Alert({
  kind = "info", children,
}: {
  kind?: "error" | "success" | "info" | "warning";
  children: ReactNode;
}) {
  return <div className={`alert alert-${kind}`} role="alert">{children}</div>;
}

export function EmptyState({
  icon, title, text, action,
}: {
  icon: ReactNode; title: string; text: string; action?: ReactNode;
}) {
  return (
    <div className="empty">
      <div className="empty-icon">{icon}</div>
      <div className="empty-title">{title}</div>
      <p className="empty-text">{text}</p>
      {action}
    </div>
  );
}

/** Maps a status string to a sensible badge color. */
export function StatusBadge({ status }: { status?: string | null }) {
  const value = (status || "unknown").toLowerCase();
  const map: Record<string, string> = {
    active: "success", live: "success", resolved: "success", qualified: "success",
    pending: "warning", in_progress: "info", paused: "warning",
    archived: "neutral", closed: "neutral", unknown: "neutral",
    discovered: "info", booking_redirected: "brand", warm_handoff: "brand",
  };
  const tone = map[value] || "neutral";
  return (
    <span className={`badge badge-${tone}`}>{value.replace(/_/g, " ")}</span>
  );
}

export function Modal({
  title, children, footer, onClose,
}: {
  title: string; children: ReactNode; footer?: ReactNode; onClose: () => void;
}) {
  return (
    <div
      className="modal-backdrop"
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
      role="dialog"
      aria-modal="true"
      aria-label={title}
    >
      <div className="modal">
        <div className="modal-header">
          <h2 className="card-title">{title}</h2>
          <button className="btn btn-ghost btn-sm" onClick={onClose} aria-label="Close">
            <IconClose />
          </button>
        </div>
        <div className="modal-body">{children}</div>
        {footer && <div className="modal-footer">{footer}</div>}
      </div>
    </div>
  );
}

/** Formats an ISO timestamp for table cells; falls back to a dash. */
export function formatDate(value?: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString(undefined, {
    month: "short", day: "numeric", year: "numeric",
    hour: "numeric", minute: "2-digit",
  });
}

export function formatRelative(value?: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";

  const seconds = Math.floor((Date.now() - date.getTime()) / 1000);
  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  if (seconds < 604800) return `${Math.floor(seconds / 86400)}d ago`;
  return formatDate(value);
}
