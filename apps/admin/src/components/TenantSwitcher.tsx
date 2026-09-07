"use client";

import { useEffect, useState } from "react";
import { api, getActiveTenant, setActiveTenant } from "@/lib/api";
import type { TenantSummary, Admin } from "@/lib/types";

/**
 * Lets a super-admin switch which tenant the dashboard is showing.
 *
 * Renders nothing for normal accounts — they only ever see their own tenant,
 * and the API rejects X-Tenant-Id from a non-super-admin regardless.
 */
export function TenantSwitcher({ admin }: { admin: Admin | null }) {
  const [tenants, setTenants] = useState<TenantSummary[]>([]);
  const [active, setActive] = useState<string>("");

  useEffect(() => {
    if (!admin?.is_super_admin) return;

    api.listTenants()
      .then((rows) => {
        setTenants(rows);
        setActive(getActiveTenant() || admin.own_tenant_id);
      })
      .catch(() => {/* switcher is non-essential; fail quietly */});
  }, [admin]);

  if (!admin?.is_super_admin || tenants.length === 0) return null;

  function switchTo(tenantId: string) {
    // Own tenant needs no header at all.
    setActiveTenant(tenantId === admin!.own_tenant_id ? null : tenantId);
    setActive(tenantId);
    // Full reload so every page refetches under the new tenant.
    window.location.reload();
  }

  const isElsewhere = active !== admin.own_tenant_id;

  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
      {isElsewhere && (
        <span className="badge badge-warning" title="You are viewing another tenant">
          Viewing client
        </span>
      )}
      <select
        className="select"
        value={active}
        onChange={(e) => switchTo(e.target.value)}
        style={{ width: "auto", minWidth: 200 }}
        aria-label="Switch tenant"
      >
        {tenants.map((t) => (
          <option key={t.id} value={t.id}>
            {t.name}
            {t.id === admin.own_tenant_id ? " (you)" : ""}
          </option>
        ))}
      </select>
    </div>
  );
}
