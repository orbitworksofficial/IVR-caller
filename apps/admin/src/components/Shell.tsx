"use client";

import { useState, useEffect } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { createClient } from "@/lib/supabase";
import { api } from "@/lib/api";
import {
  IconHome, IconBot, IconBook, IconChat, IconUsers,
  IconHandoff, IconCode, IconMenu,
} from "./Icons";

const NAV = [
  { href: "/",              label: "Overview",      icon: IconHome },
  { href: "/bots",          label: "Bots",          icon: IconBot },
  { href: "/knowledge",     label: "Knowledge",     icon: IconBook },
  { href: "/conversations", label: "Conversations", icon: IconChat },
  { href: "/leads",         label: "Leads",         icon: IconUsers },
  { href: "/handoffs",      label: "Handoffs",      icon: IconHandoff },
  { href: "/embed",         label: "Embed code",    icon: IconCode },
];

export function Shell({
  children, title, subtitle, actions,
}: {
  children: React.ReactNode;
  title: string;
  subtitle?: string;
  actions?: React.ReactNode;
}) {
  const pathname = usePathname();
  const router = useRouter();
  const [email, setEmail] = useState<string>("");
  const [pendingHandoffs, setPendingHandoffs] = useState(0);
  const [menuOpen, setMenuOpen] = useState(false);

  useEffect(() => {
    const supabase = createClient();
    supabase.auth.getUser().then(({ data }) => {
      setEmail(data.user?.email ?? "");
    });

    // The handoff badge is a nice-to-have; a failure here must not break the
    // page the admin actually asked for.
    api.stats()
      .then((stats) => setPendingHandoffs(stats.pending_handoffs))
      .catch(() => {});
  }, []);

  // Close the mobile drawer whenever the route changes.
  useEffect(() => { setMenuOpen(false); }, [pathname]);

  async function signOut() {
    const supabase = createClient();
    await supabase.auth.signOut();
    router.replace("/login");
    router.refresh();
  }

  return (
    <div className="shell">
      <div
        className={`mobile-overlay${menuOpen ? " show" : ""}`}
        onClick={() => setMenuOpen(false)}
      />

      <aside className={`sidebar${menuOpen ? " open" : ""}`}>
        <div className="sidebar-brand">
          <div className="sidebar-logo">O</div>
          <div>
            <div className="sidebar-title">OrbitWorks</div>
            <div className="sidebar-sub">Chatbot Admin</div>
          </div>
        </div>

        <nav className="sidebar-nav">
          {NAV.map(({ href, label, icon: Icon }) => {
            const active =
              href === "/" ? pathname === "/" : pathname.startsWith(href);
            return (
              <Link key={href} href={href} className={`nav-item${active ? " active" : ""}`}>
                <Icon />
                <span>{label}</span>
                {href === "/handoffs" && pendingHandoffs > 0 && (
                  <span className="nav-badge">{pendingHandoffs}</span>
                )}
              </Link>
            );
          })}
        </nav>

        <div className="sidebar-footer">
          <div className="sidebar-user">{email || "…"}</div>
          <button className="sign-out" onClick={signOut}>Sign out</button>
        </div>
      </aside>

      <div className="main">
        <div className="topbar">
          <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
            <button
              className="btn btn-ghost btn-sm menu-toggle"
              onClick={() => setMenuOpen(true)}
              aria-label="Open navigation"
            >
              <IconMenu />
            </button>
            <div>
              <h1>{title}</h1>
              {subtitle && <p className="page-subtitle">{subtitle}</p>}
            </div>
          </div>
          {actions && <div style={{ display: "flex", gap: 8 }}>{actions}</div>}
        </div>

        <div className="page">{children}</div>
      </div>
    </div>
  );
}
