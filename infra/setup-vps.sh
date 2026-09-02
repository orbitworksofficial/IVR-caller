#!/usr/bin/env bash
#
# One-time VPS setup. Run this ONCE, then use deploy.sh for every future change.
#
#     sudo bash infra/setup-vps.sh
#
# It installs Docker if missing, installs the Nginx site, and issues the TLS
# certificate. It does NOT touch your existing Nginx sites or the firewall.

set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$(pwd)"

if [ -t 1 ]; then
  R='\033[0;31m'; G='\033[0;32m'; Y='\033[1;33m'; B='\033[0;34m'; N='\033[0m'
else
  R=''; G=''; Y=''; B=''; N=''
fi
say()  { echo -e "${B}==>${N} $*"; }
ok()   { echo -e "${G}  OK${N} $*"; }
warn() { echo -e "${Y}  !!${N} $*"; }
die()  { echo -e "${R}  ERROR${N} $*" >&2; exit 1; }

API_DOMAIN="${API_DOMAIN:-chat.orb-itworks.com}"
ADMIN_DOMAIN="${ADMIN_DOMAIN:-admin.orb-itworks.com}"

echo
echo "=================================================="
echo "  OrbitWorks Chatbot — First-time VPS setup"
echo "=================================================="
echo "  API domain:   $API_DOMAIN"
echo "  Admin domain: $ADMIN_DOMAIN"
echo

[ "$(id -u)" -eq 0 ] || die "Run with sudo: sudo bash infra/setup-vps.sh"
[ -f .env ] || die ".env not found in $ROOT. Create it first (see docs/DEPLOYMENT.md step 4)."

# ── 1. DNS must resolve before certbot can work ──────────────────────────────
say "Checking DNS"

# Collect BOTH address families. A dual-stack server has each, and comparing an
# IPv4 A record against the server's IPv6 address would fail spuriously.
SERVER_IPV4="$(curl -fsS -4 --max-time 10 ifconfig.me 2>/dev/null || echo '')"
SERVER_IPV6="$(curl -fsS -6 --max-time 10 ifconfig.me 2>/dev/null || echo '')"
# Fall back to the locally configured address if the outbound lookup fails.
[ -n "$SERVER_IPV4" ] || SERVER_IPV4="$(ip -4 addr show scope global 2>/dev/null     | grep -oP '(?<=inet\s)\d+(\.\d+){3}' | head -1)"

echo "     This server IPv4: ${SERVER_IPV4:-none}"
[ -n "$SERVER_IPV6" ] && echo "     This server IPv6: $SERVER_IPV6"
[ -n "$SERVER_IPV4" ] || [ -n "$SERVER_IPV6" ]   || warn "Could not determine this server's public IP"

dns_ok=true
for d in "$API_DOMAIN" "$ADMIN_DOMAIN"; do
  # A records (IPv4) and AAAA records (IPv6) are queried separately.
  a_rec="$(dig +short A "$d" 2>/dev/null | grep -E '^[0-9]+\.' | head -1)"
  aaaa_rec="$(dig +short AAAA "$d" 2>/dev/null | grep ':' | head -1)"

  if [ -z "$a_rec" ] && [ -z "$aaaa_rec" ]; then
    warn "$d does not resolve yet"
    dns_ok=false
  elif [ -n "$a_rec" ] && [ -n "$SERVER_IPV4" ] && [ "$a_rec" = "$SERVER_IPV4" ]; then
    ok "$d -> $a_rec (IPv4)"
  elif [ -n "$aaaa_rec" ] && [ -n "$SERVER_IPV6" ] && [ "$aaaa_rec" = "$SERVER_IPV6" ]; then
    ok "$d -> $aaaa_rec (IPv6)"
  else
    warn "$d resolves to ${a_rec:-$aaaa_rec}, which is not this server"
    warn "  this server is IPv4 ${SERVER_IPV4:-none} / IPv6 ${SERVER_IPV6:-none}"
    dns_ok=false
  fi
done

if [ "$dns_ok" != true ]; then
  echo
  warn "DNS is not ready. Add A records for 'chat' and 'admin' pointing at"
  warn "$SERVER_IP, wait a few minutes, then run this script again."
  warn "Using Cloudflare? Set them to DNS-only (grey cloud) first."
  die "Stopping before certbot, which would fail."
fi

# ── 2. Docker ────────────────────────────────────────────────────────────────
say "Checking Docker"
if command -v docker >/dev/null 2>&1; then
  ok "Already installed ($(docker --version | cut -d, -f1))"
else
  say "Installing Docker"
  curl -fsSL https://get.docker.com | sh || die "Docker installation failed."
  ok "Docker installed"
fi
docker compose version >/dev/null 2>&1 || die "The docker compose plugin is missing."

# ── 3. Build and start the containers ────────────────────────────────────────
say "Building and starting containers (3-5 minutes)"
bash infra/deploy.sh || die "Container startup failed. Fix the errors above, then re-run."

# ── 4. Nginx site ────────────────────────────────────────────────────────────
say "Installing the Nginx site"
command -v nginx >/dev/null 2>&1 || die "Nginx is not installed."

SITE=/etc/nginx/sites-available/orbitworks-chatbot

# Back up an existing config so a re-run is never destructive.
if [ -f "$SITE" ]; then
  cp "$SITE" "${SITE}.bak.$(date +%s)"
  warn "Existing config backed up"
fi

sed -e "s|chat\.orb-itworks\.com|$API_DOMAIN|g" \
    -e "s|admin\.orb-itworks\.com|$ADMIN_DOMAIN|g" \
    infra/nginx-chatbot.conf > "$SITE"

ln -sf "$SITE" /etc/nginx/sites-enabled/orbitworks-chatbot

# Never reload a config that does not parse — that would take down every site.
if ! nginx -t 2>&1 | sed 's/^/     /'; then
  rm -f /etc/nginx/sites-enabled/orbitworks-chatbot
  die "Nginx config test failed. The site was removed; your other sites are untouched."
fi

systemctl reload nginx
ok "Nginx reloaded — existing sites were not interrupted"

# ── 5. TLS ───────────────────────────────────────────────────────────────────
say "Setting up HTTPS"
if ! command -v certbot >/dev/null 2>&1; then
  apt-get update -qq && apt-get install -y -qq certbot python3-certbot-nginx \
    || die "Could not install certbot."
fi

ADMIN_EMAIL="${ADMIN_EMAIL:-$(grep -E '^ESCALATION_EMAIL=' .env | cut -d= -f2 || true)}"
CERTBOT_EMAIL_FLAG="--register-unsafely-without-email"
[ -n "${ADMIN_EMAIL:-}" ] && CERTBOT_EMAIL_FLAG="--email $ADMIN_EMAIL"

if certbot --nginx -d "$API_DOMAIN" -d "$ADMIN_DOMAIN" \
     --agree-tos --non-interactive --redirect $CERTBOT_EMAIL_FLAG 2>&1 | sed 's/^/     /'; then
  ok "HTTPS enabled, with automatic renewal"
  SCHEME=https
else
  warn "Certbot failed — the site still works over plain HTTP."
  warn "Common causes: DNS not fully propagated, or Cloudflare proxy enabled."
  warn "Retry later with: sudo certbot --nginx -d $API_DOMAIN -d $ADMIN_DOMAIN"
  SCHEME=http
fi

# ── 6. Verify ────────────────────────────────────────────────────────────────
say "Verifying"
sleep 2
if curl -fsS --max-time 10 "${SCHEME}://${API_DOMAIN}/health" >/dev/null 2>&1; then
  ok "${SCHEME}://${API_DOMAIN}/health responds"
else
  warn "Could not reach ${SCHEME}://${API_DOMAIN}/health from this server"
fi

echo
echo "=================================================="
echo -e "  ${G}Setup complete${N}"
echo "=================================================="
echo
echo "  Dashboard:  ${SCHEME}://${ADMIN_DOMAIN}"
echo "  API:        ${SCHEME}://${API_DOMAIN}"
echo
echo "  IMPORTANT: if HTTPS was enabled just now, the admin dashboard still has"
echo "  the old http:// address compiled into it. Update .env so PUBLIC_API_URL"
echo "  and PUBLIC_WIDGET_URL start with https://, then run:"
echo
echo "      ./infra/deploy.sh"
echo
echo "  From now on, that one command is all you need to deploy changes."
echo
