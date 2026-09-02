# Frontend — Widget & Admin Dashboard

Two separate frontends sit on one backend, matching §08 and §10 of the
implementation plan:

| App | Stack | Purpose | Bundle |
|---|---|---|---|
| [`apps/widget`](../apps/widget) | Preact + Vite + TypeScript | The embeddable chat widget customers put on their site | **31 KB** (11.6 KB gzipped) |
| [`apps/admin`](../apps/admin) | Next.js 15 + React 19 + Supabase Auth | Tenant configuration, knowledge, leads, embed code | Server-rendered |

They are deliberately not one app. The widget must load on a stranger's
WordPress site in a few hundred milliseconds, which rules out shipping a React
framework; the admin is a real application where Next.js earns its weight.

---

## Quick start

```bash
# 1. Backend
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt
cp .env.example .env          # fill in Supabase + OpenAI values
uvicorn apps.api.main:app --reload --port 8000

# 2. Widget (built once; FastAPI serves the bundle at /widget.js)
cd apps/widget && npm install && npm run build

# 3. Admin dashboard
cd apps/admin && npm install
cp .env.local.example .env.local   # fill in Supabase + API URL
npm run dev                        # http://localhost:3000
```

Apply `database/migrations/0006_admin_and_widget_support.sql` before first use —
it adds the `tenant_admins` table the admin login depends on, plus the widget
branding columns.

### Creating the first admin account

1. Supabase dashboard → Authentication → Users → **Add user** (email + password).
2. Link that user to a tenant:

```sql
INSERT INTO tenant_admins (user_id, tenant_id, role)
VALUES ('<auth.users.id>', '<tenants.id>', 'admin');
```

Without that row the user can sign in but gets a 403 — authentication and
authorization are deliberately separate.

---

## Embedding on a customer site

```html
<script
  src="https://chat.orb-itworks.com/widget.js"
  data-bot-id="bot_abc123"
  async
></script>
```

| Attribute | Required | Description |
|---|---|---|
| `data-bot-id` | Yes | Public bot identifier. Nothing else identifies the tenant. |
| `data-api-url` | No | API origin. Defaults to the origin serving `widget.js`. |
| `data-auto-open` | No | `true` opens the panel on page load. |

The admin dashboard generates this snippet per bot on the **Embed** tab, with
platform-specific instructions for WordPress, Shopify, Webflow, Squarespace and
React.

### Why this works on any site without per-site code

- **Shadow DOM isolation** — the widget mounts into a shadow root, so the host
  page's CSS cannot restyle it and its own CSS cannot leak out. Verified against
  a page with `button { background: yellow !important; font-size: 30px !important }`.
- **Runtime configuration** — branding, greeting, modes and booking URL are
  fetched from `/widget/config` at load time. Changing a color in the dashboard
  changes the live widget; no rebuild, no redeploy, no per-customer bundle.
- **Self-mounting IIFE** — one JS file, no module loader, no framework runtime
  the host page has to provide.
- **Absolute units** — a host page with an unusual root `font-size` cannot
  rescale the panel.

---

## Widget architecture

```
apps/widget/src/
├── main.ts       Entry point: reads data-* attrs, mounts into Shadow DOM
├── Widget.tsx    The Preact UI — panel, transcript, composer, launcher
├── api.ts        Config fetch, SSE streaming, session persistence
├── styles.ts     Shadow-scoped CSS, parameterized by the bot's brand colors
└── types.ts      Shared interfaces
```

**Streaming** uses `fetch` + `ReadableStream` rather than `EventSource`, because
`EventSource` cannot be aborted cleanly — the widget cancels an in-flight reply
when the visitor closes the panel.

**Session persistence** stores a session id and conversation id in
`localStorage`, so a page refresh resumes the same conversation. Every storage
access is wrapped in `try/catch` for private-browsing mode, with an in-memory
fallback.

**Markdown** is rendered for bold, italic, inline code, bullet lists and links
only. Model output is HTML-escaped first, and only `http(s)` URLs are linkified —
never `javascript:` or `data:`.

---

## Admin dashboard

| Route | What it does |
|---|---|
| `/` | Stats, recent conversations and leads, model-routing breakdown |
| `/bots` | List and create bots |
| `/bots/[botId]` | Behavior, Branding (with live preview), Domains, Embed |
| `/knowledge` | Upload PDF/TXT/MD/CSV or paste text; delete documents |
| `/conversations` | Session list → full transcript with memory summary |
| `/leads` | Captured leads, stage filter, CSV export |
| `/handoffs` | Escalations with claim/resolve actions |
| `/embed` | Embed snippet plus a go-live checklist |
| `/login` | Supabase Auth sign-in |

Auth is enforced twice: Next.js middleware gates every route with
`supabase.auth.getUser()` (which revalidates the token rather than trusting the
cookie), and the FastAPI admin API independently validates the Bearer token and
resolves it to a tenant.

---

## Security model

The plan's critical invariant (§04) is that a bot request must never reach
another tenant's data. Three mechanisms enforce it:

1. **Server-side tenant resolution.** `ChatRequest` has no `tenant_id` field.
   The browser sends only a public `bot_id`; the server resolves
   `bot_id → tenant_id` from the `bots` table
   ([`security.resolve_bot`](../apps/api/security.py)) and uses that to set the
   RLS session variable. A caller cannot declare which tenant they are.

2. **Domain allowlisting.** Each chat request's `Origin` is checked against
   `bot_domains`. A bot with no domains registered stays open (so development
   and pre-launch testing work); once a domain is added, everything else is
   rejected with 403. `localhost` is always permitted.

3. **Rate limiting.** A sliding window per `bot_id:session_id` returns 429 past
   the threshold, so one visitor cannot burn a tenant's token budget. This is
   the application-level backstop; Cloudflare does the edge limiting.

Admin endpoints add a fourth layer: the Bearer token resolves to exactly one
tenant via `tenant_admins`, and every query is filtered by it. An authenticated
admin passing another tenant's bot id gets a 404, not that tenant's data.

---

## Verification

Both frontends were exercised in a real Chromium browser against a mock backend:

**Widget — 23/23 checks.** Shadow DOM isolation against hostile host CSS (three
separate leak tests), config-driven branding, SSE token streaming, markdown
rendering, booking CTA, session persistence across reload, mobile full-screen
layout, and confirmation that the outgoing request carries `bot_id` and
`session_id` but **no `tenant_id`**.

**Admin — 29/29 checks.** Every route renders with live API data; bot config
loads into the form; the branding preview reflects the chosen colors; the embed
snippet contains the bot id and no secrets; modals, filters and the mobile
drawer all work; zero console errors.

**Backend — 19/19 checks.** `tenant_id` is absent from the request model and
ignored when injected; message length validation; domain matching including
wildcard and suffix-confusion cases; rate limiting; and every admin endpoint
returning 401 unauthenticated.

---

## Deployment

Serve the widget bundle from a CDN path that is stable forever — customers paste
that URL into their sites and it must not change.

```bash
cd apps/widget && npm run build     # → dist/widget.js
```

FastAPI serves it at `/widget.js` for development. In production put Cloudflare
in front of it (§08) and set a long cache TTL with a versioned invalidation on
deploy.

```bash
cd apps/admin && npm run build && npm start
```

Set `NEXT_PUBLIC_WIDGET_URL` to the CDN URL so generated snippets point at the
right place.
