# VPS Deployment

Written for **your server** (`srv1926663`), based on its actual state:

| | |
|---|---|
| OS | Ubuntu 24.04.4 LTS |
| Memory | 7.8 GB total, 4.6 GB free |
| Docker | **Not installed** — step 1 installs it |
| Nginx | Running, serving ports 80 and 443 |
| Port 8000 | **Taken** by another project (`python3`) |
| Port 3000 | **Taken** by another project (`next-server`) |
| Firewall | `ufw` active — only SSH and Nginx (80/443) allowed |

Because 8000 and 3000 are in use, this deploys on **18000** and **13000**, bound
to `127.0.0.1` so only Nginx can reach them. Because ufw allows nothing but
80/443, traffic arrives through Nginx on a subdomain — **no firewall change
needed**, and you get HTTPS, which the widget requires to work on customer sites.

```
Internet → :443 Nginx ─┬─ chat.orb-itworks.com  → 127.0.0.1:18000  (API + widget)
                       └─ admin.orb-itworks.com → 127.0.0.1:13000  (dashboard)

        (your other projects keep using :8000 and :3000, untouched)
```

---

## Step 1 — DNS

In your registrar or Cloudflare, add two **A records** pointing at the VPS:

| Type | Name | Value |
|---|---|---|
| A | `chat` | *your VPS IP* |
| A | `admin` | *your VPS IP* |

Find the IP with `curl -s ifconfig.me` on the server.

> Using Cloudflare? Set both records to **DNS only** (grey cloud) until certbot
> has issued the certificate. The orange proxy cloud breaks the HTTP-01
> challenge. Switch it back on afterwards if you want.

Wait a minute or two, then confirm from the VPS:

```bash
dig +short chat.orb-itworks.com
dig +short admin.orb-itworks.com
```

Both must print your VPS IP before you continue. Certbot will fail otherwise.

---

## Step 2 — Install Docker

```bash
curl -fsSL https://get.docker.com | sudo sh
docker run --rm hello-world
```

You are running as `root`, so no `usermod` step is needed. This adds a daemon
and a `docker0` interface; your existing Nginx and Python/Next.js services are
unaffected.

---

## Step 3 — Get the code onto the server

```bash
mkdir -p /opt/orbitworks && cd /opt/orbitworks
git clone <your-repo-url> chatbot
cd chatbot
```

No git remote yet? Copy from your Windows machine instead:

```powershell
# Run from d:\chat-bot
scp -r apps ai workers scripts infra database supabase requirements.txt .dockerignore `
    root@<vps-ip>:/opt/orbitworks/chatbot/
```

---

## Step 4 — Create the .env on the server

`.env` is gitignored, so it does not travel with the code.

```bash
cd /opt/orbitworks/chatbot
nano .env
```

```env
# Supabase — Dashboard -> Settings -> API
SUPABASE_URL=https://qewwgdkeamrdlpihjmab.supabase.co
SUPABASE_KEY=<anon public key>
SUPABASE_SERVICE_KEY=<service_role secret key>

# OpenAI
OPENAI_API_KEY=<your key>

# Ports — 8000 and 3000 are taken by another project on this server
API_PORT=18000
ADMIN_PORT=13000

# Public URLs. These are compiled into the admin's BROWSER bundle at build
# time, so they must be the real public addresses, not localhost.
PUBLIC_API_URL=https://chat.orb-itworks.com
PUBLIC_WIDGET_URL=https://chat.orb-itworks.com/widget.js

ENVIRONMENT=production
WEB_CONCURRENCY=2
RATE_LIMIT_MAX_REQUESTS=20
RATE_LIMIT_WINDOW_SECONDS=60
```

It holds the service-role key — lock it down:

```bash
chmod 600 .env
```

---

## Step 5 — Build and start the containers

```bash
cd /opt/orbitworks/chatbot
docker compose -f infra/docker-compose.yml --env-file .env up -d --build
```

First build takes 3–5 minutes. Check both came up healthy:

```bash
docker compose -f infra/docker-compose.yml ps
curl -s http://127.0.0.1:18000/health
curl -s -o /dev/null -w "admin: %{http_code}\n" http://127.0.0.1:13000/login
```

`/health` should return `{"status":"healthy",...}`. If not:

```bash
docker compose -f infra/docker-compose.yml logs api --tail=50
```

---

## Step 6 — Add the Nginx site

This creates a **new** config file. Your existing sites are not touched.

```bash
cd /opt/orbitworks/chatbot
sudo cp infra/nginx-chatbot.conf /etc/nginx/sites-available/orbitworks-chatbot
sudo ln -s /etc/nginx/sites-available/orbitworks-chatbot /etc/nginx/sites-enabled/

# ALWAYS test first — a broken config takes down every site on this server
sudo nginx -t
```

Only if that prints `syntax is ok` and `test is successful`:

```bash
sudo systemctl reload nginx
```

`reload` applies the new config without dropping live connections, so your other
sites keep serving throughout.

Verify over plain HTTP before adding TLS:

```bash
curl -s http://chat.orb-itworks.com/health
```

---

## Step 7 — HTTPS

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d chat.orb-itworks.com -d admin.orb-itworks.com
```

Choose **redirect HTTP to HTTPS** when prompted. Certbot edits only the config
file from step 6 and sets up automatic renewal.

```bash
curl -s https://chat.orb-itworks.com/health
curl -s -o /dev/null -w "admin: %{http_code}\n" https://admin.orb-itworks.com/login
```

---

## Step 8 — Verify end to end

```bash
# A real grounded answer from the seeded knowledge base
curl -s -X POST https://chat.orb-itworks.com/chat \
  -H "Content-Type: application/json" \
  -d '{"bot_id":"66c7c0ec-4e99-4844-b001-296055eacbfd",
       "session_id":"vps-test-1",
       "message":"What is OrbitWorks?"}'

# Streaming should arrive progressively, not in one burst at the end
curl -N "https://chat.orb-itworks.com/chat/stream?bot_id=66c7c0ec-4e99-4844-b001-296055eacbfd&session_id=vps-test-2&message=What%20is%20AEO"

# Widget bundle
curl -s -o /dev/null -w "widget: %{http_code} (%{size_download} bytes)\n" \
     https://chat.orb-itworks.com/widget.js
```

Then open **https://admin.orb-itworks.com** and sign in.

---

## Step 9 — Go live on a website

The embed snippet, from the dashboard's **Embed** tab:

```html
<script src="https://chat.orb-itworks.com/widget.js"
        data-bot-id="66c7c0ec-4e99-4844-b001-296055eacbfd" async></script>
```

Before pointing it at a real site, add that site's domain under
**Bots → Domains** in the dashboard. Until at least one domain is registered the
bot answers requests from anywhere; once one is, every other origin gets a 403.

---

## Why this cannot break your other projects

| Risk | Mitigation |
|---|---|
| Port collision with `python3:8000` / `next-server:3000` | Uses 18000/13000 |
| Exposing services publicly | Bound to `127.0.0.1`; Nginx is the only entry |
| Breaking existing Nginx sites | New config file only; `nginx -t` before reload; `reload` not `restart` |
| Firewall changes | None needed — traffic uses 443, already allowed |
| Container name clashes | Prefixed `orbitworks-chatbot-*` |
| Disk filling from logs | Capped at 10MB × 3 per service |
| Memory pressure | ~400MB for both containers; you have 4.6GB free |
| Reboot | `restart: unless-stopped` brings them back |

---

## Updating after a code change

```bash
cd /opt/orbitworks/chatbot
git pull
docker compose -f infra/docker-compose.yml --env-file .env up -d --build
```

**Changing a `PUBLIC_*` URL requires rebuilding admin**, not just restarting it —
those values are compiled into the browser bundle:

```bash
docker compose -f infra/docker-compose.yml --env-file .env up -d --build admin
```

## Database migrations

These run against hosted Supabase, not the container — from your local machine:

```bash
npx supabase link --project-ref qewwgdkeamrdlpihjmab
npx supabase db push
```

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `port is already allocated` | Something else took 18000/13000 | `ss -tulpn \| grep LISTEN`, then change ports in `.env` |
| Certbot fails the challenge | DNS not propagated, or Cloudflare proxy on | `dig +short chat.orb-itworks.com`; set Cloudflare to DNS-only |
| `nginx -t` fails | Config error | Read the message — it names the file and line. Do **not** reload until it passes |
| 502 Bad Gateway | Container down | `docker compose ... ps` and `logs api` |
| Dashboard loads but shows zeros | `PUBLIC_API_URL` wrong at build time | Fix `.env`, rebuild **admin** |
| Reply arrives all at once | Nginx buffering the SSE stream | Confirm the `/chat/stream` block has `proxy_buffering off` |
| Widget missing on customer site | Domain not registered, or site is HTTP | Add domain in dashboard; ensure the customer site is HTTPS |
| Container exits at once | Missing env var | `docker compose ... logs api` names the variable |

```bash
# Everyday commands
docker compose -f infra/docker-compose.yml logs -f api
docker compose -f infra/docker-compose.yml restart api
docker compose -f infra/docker-compose.yml down     # stop; images kept
docker stats --no-stream
```

### Rollback

```bash
cd /opt/orbitworks/chatbot
docker compose -f infra/docker-compose.yml down   # chatbot stops; other projects unaffected

# Remove the Nginx site entirely
sudo rm /etc/nginx/sites-enabled/orbitworks-chatbot
sudo nginx -t && sudo systemctl reload nginx
```
