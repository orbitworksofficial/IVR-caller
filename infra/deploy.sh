#!/usr/bin/env bash
#
# One-command deploy. Run this on the VPS after any code change:
#
#     cd /opt/orbitworks/chatbot && ./infra/deploy.sh
#
# It pulls the latest code, rebuilds the containers, waits for them to come up
# healthy, and rolls back automatically if they do not.

set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$(pwd)"
COMPOSE="docker compose -f infra/docker-compose.yml --env-file .env"

# Colors, but only when writing to a terminal.
if [ -t 1 ]; then
  R='\033[0;31m'; G='\033[0;32m'; Y='\033[1;33m'; B='\033[0;34m'; N='\033[0m'
else
  R=''; G=''; Y=''; B=''; N=''
fi
say()  { echo -e "${B}==>${N} $*"; }
ok()   { echo -e "${G}  OK${N} $*"; }
warn() { echo -e "${Y}  !!${N} $*"; }
die()  { echo -e "${R}  ERROR${N} $*" >&2; exit 1; }

echo
echo "=============================================="
echo "  OrbitWorks Chatbot — Deploy"
echo "=============================================="
echo

# ── Preflight ────────────────────────────────────────────────────────────────
say "Checking prerequisites"
command -v docker >/dev/null 2>&1 || die "Docker is not installed. See docs/DEPLOYMENT.md step 2."
docker compose version >/dev/null 2>&1 || die "The 'docker compose' plugin is missing."
[ -f .env ] || die ".env not found in $ROOT. See docs/DEPLOYMENT.md step 4."

# Fail early with a clear message rather than a confusing build error later.
for var in SUPABASE_URL SUPABASE_KEY SUPABASE_SERVICE_KEY OPENAI_API_KEY \
           PUBLIC_API_URL PUBLIC_WIDGET_URL; do
  grep -qE "^${var}=.+" .env || die "$var is missing or empty in .env"
done
ok "Docker present, .env has all required values"

# ── Record the current version so we can roll back ───────────────────────────
PREVIOUS=""
if [ -d .git ]; then
  PREVIOUS="$(git rev-parse HEAD 2>/dev/null || true)"
fi

# ── Pull latest code ─────────────────────────────────────────────────────────
if [ -d .git ]; then
  say "Pulling latest code from git"
  BRANCH="$(git rev-parse --abbrev-ref HEAD)"

  if ! git diff --quiet || ! git diff --cached --quiet; then
    warn "You have uncommitted local changes. They are being kept."
    warn "If the pull conflicts, run: git stash"
  fi

  git pull --ff-only origin "$BRANCH" || die "git pull failed. Resolve it, then re-run."
  CURRENT="$(git rev-parse HEAD)"

  if [ "$PREVIOUS" = "$CURRENT" ]; then
    ok "Already up to date ($(git rev-parse --short HEAD)) — rebuilding anyway"
  else
    ok "Updated $(git rev-parse --short "$PREVIOUS") -> $(git rev-parse --short HEAD)"
    git log --oneline "$PREVIOUS..$CURRENT" | head -5 | sed 's/^/       /'
  fi
else
  warn "Not a git repository — skipping pull, building what is on disk"
fi

# ── Build and start ──────────────────────────────────────────────────────────
say "Building containers (first run takes 3-5 minutes)"
$COMPOSE build || die "Build failed. Scroll up for the reason."
ok "Images built"

say "Starting services"
$COMPOSE up -d || die "Failed to start containers."

# ── Wait for health ──────────────────────────────────────────────────────────
API_PORT="$(grep -E '^API_PORT=' .env | cut -d= -f2 | tr -d '[:space:]' || echo 18000)"
ADMIN_PORT="$(grep -E '^ADMIN_PORT=' .env | cut -d= -f2 | tr -d '[:space:]' || echo 13000)"
API_PORT="${API_PORT:-18000}"
ADMIN_PORT="${ADMIN_PORT:-13000}"

say "Waiting for services to become healthy"

api_up=false
for i in $(seq 1 30); do
  if curl -fsS --max-time 3 "http://127.0.0.1:${API_PORT}/health" >/dev/null 2>&1; then
    api_up=true; break
  fi
  sleep 2
done

admin_up=false
for i in $(seq 1 30); do
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 \
          "http://127.0.0.1:${ADMIN_PORT}/login" 2>/dev/null || echo 000)"
  if [ "$code" = "200" ]; then admin_up=true; break; fi
  sleep 2
done

# ── Roll back if anything failed to come up ──────────────────────────────────
if [ "$api_up" != true ] || [ "$admin_up" != true ]; then
  echo
  [ "$api_up"   != true ] && echo -e "${R}  API did not become healthy${N}"
  [ "$admin_up" != true ] && echo -e "${R}  Admin did not become healthy${N}"
  echo
  echo "Recent API logs:"
  $COMPOSE logs api --tail=30 2>&1 | sed 's/^/    /'

  if [ -n "$PREVIOUS" ] && [ -d .git ] && [ "$PREVIOUS" != "$(git rev-parse HEAD)" ]; then
    echo
    warn "Rolling back to $(git rev-parse --short "$PREVIOUS")"
    git reset --hard "$PREVIOUS" >/dev/null 2>&1
    $COMPOSE up -d --build >/dev/null 2>&1 \
      && ok "Rolled back — the previous version is running again" \
      || die "Rollback also failed. The site is DOWN; check the logs above."
  fi

  die "Deploy failed. Nothing was changed permanently."
fi

ok "API healthy on 127.0.0.1:${API_PORT}"
ok "Admin healthy on 127.0.0.1:${ADMIN_PORT}"

# ── Tidy up old images so the disk does not fill ─────────────────────────────
say "Cleaning up old images"
docker image prune -f >/dev/null 2>&1 || true
ok "Done"

# ── Summary ──────────────────────────────────────────────────────────────────
echo
echo "=============================================="
echo -e "  ${G}Deploy complete${N}"
echo "=============================================="
$COMPOSE ps --format "  {{.Name}}\t{{.Status}}" 2>/dev/null || $COMPOSE ps

PUBLIC_API="$(grep -E '^PUBLIC_API_URL=' .env | cut -d= -f2- | tr -d '[:space:]')"
echo
echo "  API:       ${PUBLIC_API:-http://127.0.0.1:$API_PORT}"
echo "  Dashboard: $(echo "${PUBLIC_API:-}" | sed 's|chat\.|admin.|')"
echo
echo "  Logs:      docker compose -f infra/docker-compose.yml logs -f api"
echo
