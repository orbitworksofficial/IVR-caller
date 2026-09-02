/**
 * Widget styles, injected into the shadow root.
 *
 * Everything is scoped to :host and inside the shadow boundary, so the host
 * page's CSS cannot reach in and the widget's rules cannot leak out. All sizes
 * are absolute (px/rem-free) because the host page may have set an unusual
 * font-size on <html> that would otherwise rescale the whole panel.
 */
export const styles = (primary: string, secondary: string) => `
:host {
  all: initial;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
               "Helvetica Neue", Arial, sans-serif;
}

*, *::before, *::after { box-sizing: border-box; }

.root {
  position: fixed;
  bottom: 20px;
  z-index: 2147483000;
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 12px;
}
.root.left  { left: 20px;  align-items: flex-start; }
.root.right { right: 20px; align-items: flex-end; }

/* ── Launcher ─────────────────────────────────────────────────────────── */
.launcher {
  display: flex;
  align-items: center;
  gap: 8px;
  height: 56px;
  min-width: 56px;
  padding: 0 18px;
  border: none;
  border-radius: 28px;
  background: ${primary};
  color: #fff;
  font-size: 15px;
  font-weight: 600;
  cursor: pointer;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.24);
  transition: transform 0.18s ease, box-shadow 0.18s ease;
}
.launcher:hover  { transform: translateY(-2px); box-shadow: 0 8px 24px rgba(0,0,0,0.28); }
.launcher:active { transform: translateY(0); }
.launcher:focus-visible { outline: 3px solid ${primary}; outline-offset: 3px; }
.launcher.icon-only { padding: 0; justify-content: center; }
.launcher svg { width: 26px; height: 26px; flex: none; }

/* ── Panel ────────────────────────────────────────────────────────────── */
.panel {
  width: 384px;
  max-width: calc(100vw - 40px);
  height: 580px;
  max-height: calc(100vh - 120px);
  display: flex;
  flex-direction: column;
  background: #fff;
  border-radius: 16px;
  overflow: hidden;
  box-shadow: 0 12px 48px rgba(0, 0, 0, 0.24);
  animation: slide-up 0.22s ease;
}
@keyframes slide-up {
  from { opacity: 0; transform: translateY(12px); }
  to   { opacity: 1; transform: translateY(0); }
}

.header {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 16px;
  background: ${secondary};
  color: #fff;
  flex: none;
}
.header-avatar {
  width: 36px; height: 36px;
  border-radius: 50%;
  background: ${primary};
  display: flex; align-items: center; justify-content: center;
  font-weight: 700; font-size: 15px;
  overflow: hidden; flex: none;
}
.header-avatar img { width: 100%; height: 100%; object-fit: cover; }
.header-text { flex: 1; min-width: 0; }
.header-name {
  font-size: 15px; font-weight: 600;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.header-status {
  font-size: 12px; opacity: 0.75;
  display: flex; align-items: center; gap: 5px;
}
.status-dot {
  width: 7px; height: 7px; border-radius: 50%;
  background: #34D399; flex: none;
}
.header-actions { display: flex; gap: 4px; }
.icon-btn {
  width: 32px; height: 32px;
  display: flex; align-items: center; justify-content: center;
  border: none; border-radius: 8px;
  background: transparent; color: #fff;
  cursor: pointer; opacity: 0.8;
  transition: background 0.15s ease, opacity 0.15s ease;
}
.icon-btn:hover { background: rgba(255,255,255,0.16); opacity: 1; }
.icon-btn:focus-visible { outline: 2px solid #fff; outline-offset: 2px; }
.icon-btn svg { width: 17px; height: 17px; }

/* ── Messages ─────────────────────────────────────────────────────────── */
.messages {
  flex: 1;
  overflow-y: auto;
  overflow-x: hidden;
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 12px;
  background: #F7F8FA;
  scrollbar-width: thin;
}
.messages::-webkit-scrollbar { width: 6px; }
.messages::-webkit-scrollbar-thumb { background: #CBD5E1; border-radius: 3px; }

.msg {
  max-width: 82%;
  padding: 10px 14px;
  border-radius: 14px;
  font-size: 14px;
  line-height: 1.55;
  white-space: pre-wrap;
  word-wrap: break-word;
  overflow-wrap: anywhere;
  animation: fade-in 0.18s ease;
}
@keyframes fade-in { from { opacity: 0; } to { opacity: 1; } }

.msg.user {
  align-self: flex-end;
  background: ${primary};
  color: #fff;
  border-bottom-right-radius: 4px;
}
.msg.assistant {
  align-self: flex-start;
  background: #fff;
  color: #1F2937;
  border: 1px solid #E5E7EB;
  border-bottom-left-radius: 4px;
}
.msg.error {
  background: #FEF2F2;
  border-color: #FECACA;
  color: #B91C1C;
}

/* Minimal markdown rendering — the model emits bold, bullets and links. */
.msg strong { font-weight: 650; }
.msg em { font-style: italic; }
.msg a { color: ${primary}; text-decoration: underline; }
.msg.user a { color: #fff; }
/* Lists come from markdown conversion, so the surrounding pre-wrap newlines
   would otherwise add a blank line before every item. */
.msg ul { margin: 6px 0; padding-left: 20px; white-space: normal; }
.msg li { margin: 2px 0; }
.msg code {
  background: rgba(0,0,0,0.06);
  padding: 1px 5px;
  border-radius: 4px;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 0.9em;
}

.typing { display: flex; gap: 4px; padding: 4px 2px; }
.typing span {
  width: 7px; height: 7px; border-radius: 50%;
  background: #9CA3AF;
  animation: bounce 1.3s infinite ease-in-out;
}
.typing span:nth-child(2) { animation-delay: 0.16s; }
.typing span:nth-child(3) { animation-delay: 0.32s; }
@keyframes bounce {
  0%, 60%, 100% { transform: translateY(0); opacity: 0.5; }
  30% { transform: translateY(-5px); opacity: 1; }
}

/* ── Booking call-to-action ───────────────────────────────────────────── */
.book-btn {
  align-self: flex-start;
  display: inline-flex;
  align-items: center;
  gap: 7px;
  padding: 9px 15px;
  border-radius: 10px;
  background: ${primary};
  color: #fff;
  font-size: 13px;
  font-weight: 600;
  text-decoration: none;
  box-shadow: 0 2px 8px rgba(0,0,0,0.12);
}
.book-btn:hover { filter: brightness(1.08); }
.book-btn svg { width: 15px; height: 15px; }

/* ── Composer ─────────────────────────────────────────────────────────── */
.composer {
  display: flex;
  align-items: flex-end;
  gap: 8px;
  padding: 12px;
  background: #fff;
  border-top: 1px solid #E5E7EB;
  flex: none;
}
.composer textarea {
  flex: 1;
  resize: none;
  border: 1px solid #D1D5DB;
  border-radius: 12px;
  padding: 10px 13px;
  font-family: inherit;
  font-size: 14px;
  line-height: 1.45;
  max-height: 110px;
  min-height: 42px;
  color: #1F2937;
  background: #fff;
  transition: border-color 0.15s ease, box-shadow 0.15s ease;
}
.composer textarea:focus {
  outline: none;
  border-color: ${primary};
  box-shadow: 0 0 0 3px ${primary}22;
}
.composer textarea::placeholder { color: #9CA3AF; }
.composer textarea:disabled { background: #F3F4F6; cursor: not-allowed; }

.send-btn {
  width: 42px; height: 42px;
  display: flex; align-items: center; justify-content: center;
  border: none; border-radius: 12px;
  background: ${primary}; color: #fff;
  cursor: pointer; flex: none;
  transition: opacity 0.15s ease, filter 0.15s ease;
}
.send-btn:hover:not(:disabled) { filter: brightness(1.08); }
.send-btn:disabled { opacity: 0.4; cursor: not-allowed; }
.send-btn:focus-visible { outline: 2px solid ${primary}; outline-offset: 2px; }
.send-btn svg { width: 19px; height: 19px; }

.footer {
  padding: 7px 12px 11px;
  text-align: center;
  font-size: 11px;
  color: #9CA3AF;
  background: #fff;
  flex: none;
}

.error-banner {
  margin: 0 16px;
  padding: 9px 12px;
  border-radius: 8px;
  background: #FEF2F2;
  border: 1px solid #FECACA;
  color: #B91C1C;
  font-size: 12.5px;
}

/* ── Mobile: full-screen panel ────────────────────────────────────────── */
@media (max-width: 480px) {
  .root { bottom: 0; left: 0; right: 0; align-items: stretch; gap: 0; }
  .root.open .launcher { display: none; }
  .panel {
    width: 100vw;
    max-width: 100vw;
    height: 100dvh;
    max-height: 100dvh;
    border-radius: 0;
  }
  .root:not(.open) { bottom: 20px; left: auto; right: 20px; }
  .root:not(.open).left { left: 20px; right: auto; }
}

@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: 0.01ms !important;
    transition-duration: 0.01ms !important;
  }
}
`;
