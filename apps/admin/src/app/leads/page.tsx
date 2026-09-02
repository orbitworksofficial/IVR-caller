"use client";

import { useEffect, useState } from "react";
import { Shell } from "@/components/Shell";
import {
  Alert, LoadingState, EmptyState, StatusBadge, formatDate,
} from "@/components/ui";
import { IconUsers } from "@/components/Icons";
import { api, ApiError } from "@/lib/api";
import type { Lead } from "@/lib/types";

/** Converts the lead list to CSV so the sales team can work it in a spreadsheet. */
function toCsv(leads: Lead[]): string {
  const header = [
    "Name", "Email", "Company", "Stage", "Services", "Requirement", "Captured",
  ];

  const escape = (value: unknown) => {
    const text = value == null ? "" : String(value);
    // Quote anything containing a delimiter, quote or newline.
    return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
  };

  const rows = leads.map((lead) => [
    lead.name, lead.email, lead.company, lead.lead_stage,
    (lead.services_of_interest ?? []).join("; "),
    lead.requirement, lead.created_at,
  ].map(escape).join(","));

  return [header.join(","), ...rows].join("\n");
}

export default function LeadsPage() {
  const [leads, setLeads] = useState<Lead[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [stageFilter, setStageFilter] = useState("all");

  useEffect(() => {
    api.listLeads()
      .then(setLeads)
      .catch((e: ApiError) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  const stages = Array.from(
    new Set(leads.map((l) => l.lead_stage).filter(Boolean))
  ) as string[];

  const visible = stageFilter === "all"
    ? leads
    : leads.filter((l) => l.lead_stage === stageFilter);

  function exportCsv() {
    const blob = new Blob([toCsv(visible)], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `leads-${new Date().toISOString().slice(0, 10)}.csv`;
    link.click();
    URL.revokeObjectURL(url);
  }

  return (
    <Shell
      title="Leads"
      subtitle="Captured automatically by the consultative sales flow."
      actions={
        leads.length > 0 ? (
          <button className="btn btn-secondary" onClick={exportCsv}>
            Export CSV
          </button>
        ) : undefined
      }
    >
      {error && <Alert kind="error">{error}</Alert>}

      {loading ? (
        <LoadingState label="Loading leads…" />
      ) : leads.length === 0 ? (
        <div className="card">
          <EmptyState
            icon={<IconUsers />}
            title="No leads yet"
            text="When a visitor shares their details or asks to speak with your team, the bot captures them here automatically."
          />
        </div>
      ) : (
        <>
          {stages.length > 0 && (
            <div style={{ display: "flex", gap: 6, marginBottom: 16, flexWrap: "wrap" }}>
              <button
                className={`btn btn-sm ${stageFilter === "all" ? "btn-primary" : "btn-secondary"}`}
                onClick={() => setStageFilter("all")}
              >
                All ({leads.length})
              </button>
              {stages.map((stage) => (
                <button
                  key={stage}
                  className={`btn btn-sm ${stageFilter === stage ? "btn-primary" : "btn-secondary"}`}
                  onClick={() => setStageFilter(stage)}
                >
                  {stage.replace(/_/g, " ")} (
                  {leads.filter((l) => l.lead_stage === stage).length})
                </button>
              ))}
            </div>
          )}

          <div className="card">
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Contact</th><th>Company</th><th>Stage</th>
                    <th>Interest</th><th>Captured</th>
                  </tr>
                </thead>
                <tbody>
                  {visible.map((lead) => (
                    <tr key={lead.id}>
                      <td>
                        <div className="cell-strong">{lead.name || "Unnamed"}</div>
                        {lead.email && (
                          <a href={`mailto:${lead.email}`} style={{ fontSize: 12.5 }}>
                            {lead.email}
                          </a>
                        )}
                      </td>
                      <td className="cell-muted">{lead.company || "—"}</td>
                      <td><StatusBadge status={lead.lead_stage} /></td>
                      <td style={{ maxWidth: 280 }}>
                        {lead.services_of_interest?.length ? (
                          <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
                            {lead.services_of_interest.map((service) => (
                              <span key={service} className="badge badge-neutral">
                                {service}
                              </span>
                            ))}
                          </div>
                        ) : (
                          <span className="cell-muted">
                            {lead.requirement
                              ? lead.requirement.slice(0, 90) +
                                (lead.requirement.length > 90 ? "…" : "")
                              : "—"}
                          </span>
                        )}
                      </td>
                      <td className="cell-muted">{formatDate(lead.created_at)}</td>
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
