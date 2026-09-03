#!/usr/bin/env bash
#
# Removes the GitHub username/token prompt from every git pull.
#
#     bash infra/setup-git-ssh.sh
#
# Generates an SSH key on this server, shows you the public half to paste into
# GitHub, then switches the repo from HTTPS to SSH. Run once; after that
# ./infra/deploy.sh never asks for credentials again.

set -euo pipefail

cd "$(dirname "$0")/.."

if [ -t 1 ]; then
  R='\033[0;31m'; G='\033[0;32m'; Y='\033[1;33m'; B='\033[0;34m'; N='\033[0m'
else
  R=''; G=''; Y=''; B=''; N=''
fi
say()  { echo -e "${B}==>${N} $*"; }
ok()   { echo -e "${G}  OK${N} $*"; }
warn() { echo -e "${Y}  !!${N} $*"; }
die()  { echo -e "${R}  ERROR${N} $*" >&2; exit 1; }

KEY="$HOME/.ssh/id_ed25519"

echo
echo "=================================================="
echo "  Passwordless GitHub access"
echo "=================================================="
echo

# ── 1. Key ───────────────────────────────────────────────────────────────────
if [ -f "$KEY" ]; then
  ok "SSH key already exists at $KEY"
else
  say "Generating an SSH key for this server"
  mkdir -p "$HOME/.ssh" && chmod 700 "$HOME/.ssh"
  # No passphrase: an unattended deploy script cannot type one.
  ssh-keygen -t ed25519 -C "orbitworks-vps-$(hostname)" -f "$KEY" -N "" -q
  ok "Key created"
fi

# ── 2. Show the public half ──────────────────────────────────────────────────
echo
echo "=================================================="
echo -e "  ${Y}ACTION NEEDED — copy the line below${N}"
echo "=================================================="
echo
cat "${KEY}.pub"
echo
echo "=================================================="
echo
echo "  1. Copy that whole line (starts 'ssh-ed25519', ends with the hostname)"
echo "  2. Open:  https://github.com/settings/keys"
echo "  3. Click: New SSH key"
echo "  4. Title: orbitworks-vps"
echo "  5. Key:   paste the line"
echo "  6. Click: Add SSH key"
echo
read -r -p "  Press Enter once you have added it to GitHub... " _

# ── 3. Trust github.com so the first connection does not prompt ──────────────
say "Adding github.com to known_hosts"
touch "$HOME/.ssh/known_hosts" && chmod 600 "$HOME/.ssh/known_hosts"
ssh-keygen -F github.com >/dev/null 2>&1 \
  || ssh-keyscan -t rsa,ecdsa,ed25519 github.com >> "$HOME/.ssh/known_hosts" 2>/dev/null
ok "known_hosts updated"

# ── 4. Test the key ──────────────────────────────────────────────────────────
say "Testing the connection to GitHub"
# `ssh -T git@github.com` always exits 1 even on success, so match the greeting.
AUTH_OUTPUT="$(ssh -T -o StrictHostKeyChecking=no -o BatchMode=yes \
                 git@github.com 2>&1 || true)"

if echo "$AUTH_OUTPUT" | grep -q "successfully authenticated"; then
  ok "$(echo "$AUTH_OUTPUT" | head -1)"
else
  echo "$AUTH_OUTPUT" | sed 's/^/     /'
  die "GitHub did not accept the key. Confirm you pasted the whole line at https://github.com/settings/keys, then run this script again."
fi

# ── 5. Switch the repo from HTTPS to SSH ─────────────────────────────────────
say "Switching the repository to SSH"
CURRENT="$(git remote get-url origin 2>/dev/null || echo '')"
echo "     was: ${CURRENT:-none}"

if echo "$CURRENT" | grep -q '^git@github.com:'; then
  ok "Already using SSH"
else
  # https://github.com/owner/repo.git -> git@github.com:owner/repo.git
  SSH_URL="$(echo "$CURRENT" | sed -E 's#^https://github\.com/#git@github.com:#')"
  case "$SSH_URL" in
    *.git) ;;
    *) SSH_URL="${SSH_URL}.git" ;;
  esac
  [ -n "$SSH_URL" ] || die "Could not work out the SSH URL from '$CURRENT'."
  git remote set-url origin "$SSH_URL"
  ok "now: $SSH_URL"
fi

# ── 6. Prove a pull works without prompting ──────────────────────────────────
say "Testing git fetch (should not ask for anything)"
if git fetch origin --quiet 2>&1; then
  ok "Fetch succeeded with no credentials"
else
  die "Fetch failed. Check the repository URL and that the key has access."
fi

echo
echo "=================================================="
echo -e "  ${G}Done${N}"
echo "=================================================="
echo
echo "  Deploying is now one command, with no login:"
echo
echo "      cd /opt/orbitworks/chatbot && ./infra/deploy.sh"
echo
echo "  Shorter still — run this once to add a 'deploy' shortcut:"
echo
echo "      echo \"alias deploy='cd /opt/orbitworks/chatbot && ./infra/deploy.sh'\" >> ~/.bashrc && source ~/.bashrc"
echo
echo "  Then just type:  deploy"
echo
