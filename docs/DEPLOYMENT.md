# VPS Deployment

Deploys the chatbot to a VPS that is **already running other projects**, using
Docker Compose (per plan §08 — no Kubernetes). Everything is scoped so it cannot
disturb what is already there.

Target for this first pass: reachable at `http://<vps-ip>:18000`, no domain or
TLS yet. See [Adding a domain](#adding-a-domain-later) for the next step.

---

## Step 0 — Investigate the VPS first

Run these on the VPS. **All are read-only** — nothing is installed or changed.
Send me the output and I will tailor the rest.

```bash
# What OS and how much memory?
cat /etc/os-release | head -2
free -h | head -2
df -h / | tail -1

# Is Docker installed, and can your user run it?
docker --version 2>/dev/null || echo "docker: NOT INSTALLED"
docker compose version 2>/dev/null || echo "compose plugin: NOT INSTALLED"
docker ps --format '{{.Names}}\t{{.Image}}\t{{.Ports}}' 2>/dev/null || echo "(cannot list containers)"

# What web server is in front, if any?
which nginx apache2 httpd caddy 2>/dev/null || echo "no web server found on PATH"
systemctl is-active nginx apache2 2>/dev/null

# Which ports are already taken? (the important one)
ss -tulpn | grep LISTEN

# Is the firewall filtering inbound ports?
ufw status 2>/dev/null || iptables -L INPUT -n 2>/dev/null | head -5 || echo "(no ufw/iptables access)"
```

The critical line is `ss -tulpn`. This deployment uses **18000** and **13000**;
if either is already listening, change `API_PORT` / `ADMIN_PORT` in `.env`.

### If Docker is not installed

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
# Log out and back in for the group change to apply, then verify:
docker run --rm hello-world
```

Installing Docker does not disturb existing non-Docker sites — it only adds a
daemon and a `docker0` network interface.

---

## Step 1 — Get the code onto the VPS

```bash
sudo mkdir -p /opt/orbitworks && sudo chown $USER:$USER /opt/orbitworks
cd /opt/orbitworks
git clone <your-repo-url> chatbot
cd chatbot
```

No repo yet? Copy from your machine instead:

```bash
# From d:\chat-bot on Windows
scp -r apps ai workers scripts infra database supabase requirements.txt \
    user@your-vps-ip:/opt/orbitworks/chatbot/
```

---

## Step 2 — Create the .env on the server

`.env` is gitignored, so it never arrives with the code. Create it on the VPS:

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

# Ports on the VPS — change if `ss -tulpn` shows these are taken
API_PORT=18000
ADMIN_PORT=13000

# How the browser reaches the API. MUST be the public address, not localhost —
# these are compiled into the admin's browser bundle at build time.
PUBLIC_API_URL=http://<your-vps-ip>:18000
PUBLIC_WIDGET_URL=http://<your-vps-ip>:18000/widget.js

ENVIRONMENT=production
WEB_CONCURRENCY=2
RATE_LIMIT_MAX_REQUESTS=20
RATE_LIMIT_WINDOW_SECONDS=60
```

Lock it down — it holds the service-role key:

```bash
chmod 600 .env
```

---

## Step 3 — Build and start

```bash
cd /opt/orbitworks/chatbot
docker compose -f infra/docker-compose.yml --env-file .env up -d --build
```

First build takes 3–5 minutes. Then:

```bash
docker compose -f infra/docker-compose.yml ps
docker compose -f infra/docker-compose.yml logs -f --tail=50
```

---

## Step 4 — Verify it actually works

```bash
# From the VPS
curl -s http://localhost:18000/health
curl -s -o /dev/null -w "widget: %{http_code} (%{size_download} bytes)\n" \
     http://localhost:18000/widget.js
curl -s -o /dev/null -w "admin: %{http_code}\n" http://localhost:13000/login

# A real grounded answer, end to end
curl -s -X POST http://localhost:18000/chat \
  -H "Content-Type: application/json" \
  -d '{"bot_id":"66c7c0ec-4e99-4844-b001-296055eacbfd",
       "session_id":"vps-test-1",
       "message":"What is OrbitWorks?"}'
```

Then from your own machine, confirming the firewall allows it:

```bash
curl -s http://<your-vps-ip>:18000/health
```

If that last one hangs, open the ports:

```bash
sudo ufw allow 18000/tcp
sudo ufw allow 13000/tcp
```

---

## Step 5 — Use it

- **Dashboard**: `http://<your-vps-ip>:13000`
- **Embed snippet** for any customer site:

```html
<script src="http://<your-vps-ip>:18000/widget.js"
        data-bot-id="66c7c0ec-4e99-4844-b001-296055eacbfd" async></script>
```

> **This only works on `http://` sites.** A browser on an HTTPS page blocks
> requests to plain HTTP (mixed content), so the widget will silently fail to
> load on any secure site. Fine for testing; see below before going live.

---

## Why this cannot disturb your other projects

| Concern | How it is handled |
|---|---|
| Port collisions | Uses 18000/13000, not 80/443/3000/8000. Change in `.env` if taken. |
| Container name clashes | Prefixed `orbitworks-chatbot-*`; compose project name is `orbitworks-chatbot`. |
| Network isolation | Own bridge network `orbitworks-chatbot-net`. |
| Disk from logs | JSON logs capped at 10MB × 3 per service. |
| Reboots | `restart: unless-stopped` brings both back automatically. |
| Nginx/Apache configs | Untouched — nothing is written outside this directory. |

---

## Adding a domain later

Once `chat.orb-itworks.com` points at the VPS:

**1.** Bind the containers to localhost only, so the proxy is the sole entry point:

```env
API_BIND=127.0.0.1
ADMIN_BIND=127.0.0.1
```

**2.** Add an Nginx server block (a *new* file — do not edit existing sites):

```nginx
# /etc/nginx/sites-available/orbitworks-chatbot
server {
    listen 80;
    server_name chat.orb-itworks.com;

    location / {
        proxy_pass http://127.0.0.1:18000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # Required for SSE streaming (/chat/stream) — without these the
        # response is buffered and tokens arrive all at once at the end.
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 300s;
    }
}

server {
    listen 80;
    server_name admin.orb-itworks.com;

    location / {
        proxy_pass http://127.0.0.1:13000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

```bash
sudo ln -s /etc/nginx/sites-available/orbitworks-chatbot /etc/nginx/sites-enabled/
sudo nginx -t          # ALWAYS test before reloading — a bad config takes down every site
sudo systemctl reload nginx
```

**3.** TLS, which the widget needs to work on HTTPS customer sites:

```bash
sudo certbot --nginx -d chat.orb-itworks.com -d admin.orb-itworks.com
```

**4.** Update `.env` to the public HTTPS URLs and rebuild the admin — the old
values are baked into its browser bundle:

```env
PUBLIC_API_URL=https://chat.orb-itworks.com
PUBLIC_WIDGET_URL=https://chat.orb-itworks.com/widget.js
```

```bash
docker compose -f infra/docker-compose.yml --env-file .env up -d --build admin
```

**5.** Register the customer's domain in the dashboard under
**Bots → Domains**. Until a domain is registered the bot answers requests from
any site; once one is, everything else gets a 403.

---

## Updating after a code change

```bash
cd /opt/orbitworks/chatbot
git pull
docker compose -f infra/docker-compose.yml --env-file .env up -d --build
```

Rebuild only what changed:

```bash
docker compose -f infra/docker-compose.yml --env-file .env up -d --build api
```

## Database migrations

Migrations run against hosted Supabase, not the container:

```bash
npx supabase link --project-ref qewwgdkeamrdlpihjmab
npx supabase db push
```

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `port is already allocated` | Another project owns it | Change `API_PORT`/`ADMIN_PORT` in `.env` |
| Health check fails, logs show Supabase errors | Wrong or missing keys | Check `.env`; service key must be `service_role`, not anon |
| Dashboard loads but shows all zeros | `PUBLIC_API_URL` wrong at build time | Fix `.env`, then rebuild **admin** (not just restart) |
| Widget doesn't appear on a customer site | HTTPS page loading HTTP widget | Set up a domain and TLS |
| Widget loads but chat 403s | Origin not in `bot_domains` | Add the domain in the dashboard |
| Replies arrive all at once, not streamed | Nginx buffering | `proxy_buffering off` in the location block |
| Container exits immediately | Missing required env var | `docker compose logs api` names the variable |

```bash
# Useful commands
docker compose -f infra/docker-compose.yml logs -f api
docker compose -f infra/docker-compose.yml restart api
docker compose -f infra/docker-compose.yml down          # stop (keeps images)
docker stats --no-stream                                  # resource usage
```
