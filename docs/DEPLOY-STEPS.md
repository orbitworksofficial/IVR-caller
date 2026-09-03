# Deploy to your VPS — step by step

Follow these in order. **Copy each command exactly**, run it on the server, and
check you got the expected result before moving on.

After the one-time setup, deploying any future change is a single command:

```bash
cd /opt/orbitworks/chatbot && ./infra/deploy.sh
```

---

## Before you start

You need:
- Your VPS login (`ssh root@YOUR_SERVER_IP`)
- Access to your domain's DNS settings
- Your Supabase keys and OpenAI key (from the `.env` on your Windows machine)

Throughout, replace `YOUR_SERVER_IP` with your actual IP. Find it by running
this on the VPS:

```bash
curl -s ifconfig.me
```

---

## STEP 1 — Point two subdomains at your server

In your domain registrar (or Cloudflare), add **two A records**:

| Type | Name | Value |
|---|---|---|
| A | `chat` | YOUR_SERVER_IP |
| A | `admin` | YOUR_SERVER_IP |

**Using Cloudflare?** Set both to **DNS only** — click the orange cloud so it
turns grey. The orange proxy blocks the certificate check in step 6. You can
turn it back on afterwards.

Wait about 5 minutes, then check on the VPS:

```bash
dig +short chat.orb-itworks.com
dig +short admin.orb-itworks.com
```

**Expected:** both print your server's IP.
**If blank:** DNS hasn't propagated. Wait 10 more minutes and try again. Do not
continue until both work — step 6 will fail otherwise.

---

## STEP 2 — Put your code on GitHub

The deploy script pulls from git, so the code needs to live there.

Run these **on your Windows machine**, in `d:\chat-bot`:

```bash
git remote -v
```

**If that prints a URL**, you already have a remote. Push your work:

```bash
git push origin main
```

**If it prints nothing**, create a repository first:

1. Go to https://github.com/new
2. Name it `orbitworks-chatbot`
3. Choose **Private** (this code holds your business logic)
4. Do **not** tick "Add a README"
5. Click **Create repository**

Then connect and push:

```bash
git remote add origin https://github.com/YOUR_USERNAME/orbitworks-chatbot.git
git branch -M main
git push -u origin main
```

**Expected:** GitHub shows your files.
**Note:** your `.env` is deliberately *not* uploaded — it holds secrets. You'll
create it directly on the server in step 4.

---

## STEP 3 — Download the code onto the VPS

Connect to your server:

```bash
ssh root@YOUR_SERVER_IP
```

Then:

```bash
mkdir -p /opt/orbitworks
cd /opt/orbitworks
git clone https://github.com/YOUR_USERNAME/orbitworks-chatbot.git chatbot
cd chatbot
```

GitHub will ask for a username and password. **The password is not your GitHub
password** — it's a personal access token:

1. Go to https://github.com/settings/tokens
2. **Generate new token (classic)**
3. Tick the **repo** checkbox
4. Generate, then copy the token — paste it as the password

**Expected:** `ls` shows folders including `apps`, `ai`, `infra`.

---

## STEP 4 — Create the secrets file on the server

This file holds your keys. It never goes into git.

```bash
cd /opt/orbitworks/chatbot
nano .env
```

Paste this, replacing each `<...>` with your real value from the `.env` on your
Windows machine:

```env
SUPABASE_URL=https://qewwgdkeamrdlpihjmab.supabase.co
SUPABASE_KEY=<your anon key>
SUPABASE_SERVICE_KEY=<your service_role key>
OPENAI_API_KEY=<your OpenAI key>

API_PORT=18000
ADMIN_PORT=13000

PUBLIC_API_URL=https://chat.orb-itworks.com
PUBLIC_WIDGET_URL=https://chat.orb-itworks.com/widget.js

ENVIRONMENT=production
WEB_CONCURRENCY=2
RATE_LIMIT_MAX_REQUESTS=20
RATE_LIMIT_WINDOW_SECONDS=60
```

Save and exit nano: **Ctrl+O**, **Enter**, then **Ctrl+X**.

Then protect the file — it contains a key that bypasses all database security:

```bash
chmod 600 .env
```

Check it looks right (this hides the secret values):

```bash
sed -E 's/=(.{8}).*/=\1.../' .env
```

**Expected:** every line has a value after the `=`, none still say `<your...>`.

---

## STEP 5 — Run the one-time setup

This installs Docker, builds everything, configures Nginx and sets up HTTPS.
It takes about 5 minutes.

```bash
cd /opt/orbitworks/chatbot
sudo bash infra/setup-vps.sh
```

**Expected:** it ends with `Setup complete` and shows your two URLs.

**If it stops at the DNS check:** step 1 isn't finished. Wait and re-run.
**If it stops elsewhere:** copy the error message and send it to me.

This script is safe to run again if something fails partway.

---

## STEP 6 — Confirm HTTPS, then rebuild once

The setup script sets up HTTPS at the end — but the dashboard was built moments
earlier with the `http://` address baked in. One rebuild fixes it.

First check HTTPS is live:

```bash
curl -s https://chat.orb-itworks.com/health
```

**Expected:** `{"status":"healthy","version":"1.1.0",...}`

If you see that, `.env` is already correct (you wrote `https://` in step 4), so
just rebuild:

```bash
cd /opt/orbitworks/chatbot
./infra/deploy.sh
```

**Expected:** `Deploy complete`, both services listed as running.

---

## STEP 7 — Test that it actually works

```bash
# Ask the bot a real question
curl -s -X POST https://chat.orb-itworks.com/chat \
  -H "Content-Type: application/json" \
  -d '{"bot_id":"66c7c0ec-4e99-4844-b001-296055eacbfd",
       "session_id":"test-1",
       "message":"What is OrbitWorks?"}'
```

**Expected:** a real answer about OrbitWorks being an AI automation and IT
services company.

Then open **https://admin.orb-itworks.com** in your browser and sign in with
the account created earlier.

**Expected:** the dashboard, showing 12 knowledge chunks and 1 bot.

---

## STEP 8 — Put the chatbot on a website

In the dashboard, go to **Embed code** and copy the snippet:

```html
<script src="https://chat.orb-itworks.com/widget.js"
        data-bot-id="66c7c0ec-4e99-4844-b001-296055eacbfd" async></script>
```

Paste it into any website, just before the closing `</body>` tag.

**Before using it on a real site**, go to **Bots → Domains** and add that site's
domain. Until you add one, any website can use your bot and spend your OpenAI
credit. Once you add one, every other site is blocked.

---

# From now on: deploying changes

Whenever code changes, this is the whole process.

**On your Windows machine:**

```bash
git add -A
git commit -m "describe what changed"
git push
```

**On the VPS:**

```bash
cd /opt/orbitworks/chatbot && ./infra/deploy.sh
```

That's it. The script pulls the new code, rebuilds, waits for both services to
respond, and — if anything fails to start — **automatically puts the previous
working version back**. A broken deploy will not take your site down.

### Make it even shorter

Run this once on the VPS:

```bash
echo "alias deploy='cd /opt/orbitworks/chatbot && ./infra/deploy.sh'" >> ~/.bashrc
source ~/.bashrc
```

Now deploying from anywhere on the server is just:

```bash
deploy
```

---

## Useful commands

```bash
cd /opt/orbitworks/chatbot

# Watch the logs live (Ctrl+C to stop)
./infra/dc logs -f api

# Is everything running?
./infra/dc ps

# Restart without rebuilding
./infra/dc restart

# Stop the chatbot (your other projects keep running)
./infra/dc down

# Start it again
./infra/deploy.sh
```

---

## If something breaks

**Site shows 502 Bad Gateway**
The container stopped. Check why, then restart:
```bash
./infra/dc logs api --tail=50
./infra/deploy.sh
```

**Dashboard loads but everything shows 0**
`PUBLIC_API_URL` in `.env` is wrong. Fix it, then rebuild — restarting is not
enough, because that value is compiled into the dashboard:
```bash
nano .env
./infra/deploy.sh
```

**Deploy failed and rolled back**
Your site is still up on the previous version. The error is in the output above
the rollback message — send it to me.

**Bot replies "I don't have that information" to everything**
The knowledge base is empty or doesn't cover the question. Add content in the
dashboard under **Knowledge**.

**Nothing works and you want to undo everything**
```bash
cd /opt/orbitworks/chatbot
./infra/dc down
sudo rm /etc/nginx/sites-enabled/orbitworks-chatbot
sudo nginx -t && sudo systemctl reload nginx
```
Your other projects are unaffected — they were never touched.
