"""
Widget-facing security layer.

Implements the tenant resolution flow from the implementation plan (§04):
    widget data-bot-id -> bot_id -> tenant_id -> allowed domain -> bot config

Critical invariant: the browser only ever sends a PUBLIC bot_id. The tenant_id
is derived server-side from that bot_id and never accepted from the client —
otherwise any visitor could claim another tenant's identity and RLS would
faithfully hand over their data.
"""

import os
import time
import logging
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from fastapi import HTTPException, Request
from supabase import Client

logger = logging.getLogger(__name__)

# Resolved bot records are cached briefly — /chat hits this on every message
# and the bots table changes rarely.
_BOT_CACHE: Dict[str, tuple[float, Dict[str, Any]]] = {}
_BOT_CACHE_TTL = 60.0  # seconds

# In-process sliding-window rate limiter. Cloudflare handles the edge (§08);
# this is the application-level backstop so a single session cannot burn a
# tenant's token budget if it gets past the CDN.
_RATE_BUCKETS: Dict[str, list[float]] = {}
RATE_LIMIT_MAX_REQUESTS = int(os.getenv("RATE_LIMIT_MAX_REQUESTS", "20"))
RATE_LIMIT_WINDOW_SECONDS = float(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "60"))


def _normalize_host(value: str) -> str:
    """
    Reduces an Origin header or a stored domain to a bare lowercase hostname.
    Accepts 'https://x.com', 'x.com', 'x.com:3000' and '*.x.com' alike.
    """
    value = (value or "").strip().lower()
    if not value:
        return ""
    if "//" not in value:
        value = "//" + value
    host = urlparse(value).hostname or ""
    return host


def _host_matches(origin_host: str, allowed: str) -> bool:
    """
    Matches an origin hostname against one allowed-domain entry.
    Supports exact matches and a leading '*.' wildcard for subdomains.
    """
    allowed = (allowed or "").strip().lower()
    if not allowed:
        return False

    if allowed.startswith("*."):
        suffix = allowed[1:]  # '.example.com'
        base = allowed[2:]    # 'example.com'
        return origin_host == base or origin_host.endswith(suffix)

    return origin_host == _normalize_host(allowed)


async def resolve_bot(supabase: Client, bot_id: str) -> Dict[str, Any]:
    """
    Resolves a public bot_id to its full record — including the authoritative
    tenant_id. Raises 404 if the bot does not exist or is not active.

    This is the ONLY approved way to obtain a tenant_id for a widget request.
    """
    cached = _BOT_CACHE.get(bot_id)
    if cached and (time.time() - cached[0]) < _BOT_CACHE_TTL:
        return cached[1]

    try:
        response = supabase.table("bots").select("*").eq("id", bot_id).execute()
    except Exception as e:
        # An invalid UUID string reaches Postgres as a cast error — treat it as
        # "not found" rather than leaking a 500 with database internals.
        logger.warning(f"Bot lookup failed for '{bot_id}': {e}")
        raise HTTPException(status_code=404, detail="Bot not found.")

    if not response.data:
        raise HTTPException(status_code=404, detail="Bot not found.")

    bot = response.data[0]

    # A bot that has been paused or archived must stop answering immediately.
    status = (bot.get("status") or "active").lower()
    if status not in ("active", "live"):
        raise HTTPException(status_code=403, detail="This bot is not currently active.")

    if not bot.get("tenant_id"):
        logger.error(f"Bot '{bot_id}' has no tenant_id — refusing to serve.")
        raise HTTPException(status_code=500, detail="Bot is misconfigured.")

    _BOT_CACHE[bot_id] = (time.time(), bot)
    return bot


async def enforce_allowed_domain(
    supabase: Client, bot_id: str, request: Request
) -> None:
    """
    Validates the browser's Origin against the bot's allowed domains (§08).

    Policy:
    - No bot_domains rows configured -> allow (the bot has not been locked down
      yet; this keeps local development and pre-launch testing workable).
    - Rows configured + no/!matching Origin -> 403.

    Server-to-server callers (curl, tests, the eval runner) send no Origin
    header at all; those are allowed through so the widget restriction does not
    break the existing Python tooling.
    """
    origin = request.headers.get("origin") or request.headers.get("referer")
    if not origin:
        return  # non-browser caller

    try:
        resp = supabase.table("bot_domains").select("domain").eq("bot_id", bot_id).execute()
    except Exception as e:
        logger.warning(f"bot_domains lookup failed for '{bot_id}': {e} — allowing request.")
        return

    allowed_domains = [r.get("domain") for r in (resp.data or []) if r.get("domain")]
    if not allowed_domains:
        return  # not locked down yet

    origin_host = _normalize_host(origin)

    # Always permit local development hosts so the Next.js dev server and the
    # widget playground work without registering a domain first.
    if origin_host in ("localhost", "127.0.0.1", "0.0.0.0"):
        return

    for allowed in allowed_domains:
        if _host_matches(origin_host, allowed):
            return

    logger.warning(
        f"Blocked widget request for bot '{bot_id}' from unauthorized origin '{origin_host}'."
    )
    raise HTTPException(
        status_code=403,
        detail="This chatbot is not authorized for this domain.",
    )


def enforce_rate_limit(key: str) -> None:
    """
    Sliding-window rate limit, keyed per bot+session. Raises 429 when exceeded.

    Note: this is per-process state. Behind multiple workers the effective limit
    scales with worker count — acceptable as a backstop given Cloudflare does
    the real edge limiting, but move to Redis if the API is scaled out widely.
    """
    now = time.time()
    window_start = now - RATE_LIMIT_WINDOW_SECONDS

    bucket = _RATE_BUCKETS.setdefault(key, [])
    # Drop timestamps that have aged out of the window
    bucket[:] = [t for t in bucket if t > window_start]

    if len(bucket) >= RATE_LIMIT_MAX_REQUESTS:
        retry_after = int(bucket[0] + RATE_LIMIT_WINDOW_SECONDS - now) + 1
        logger.warning(f"Rate limit exceeded for '{key}'.")
        raise HTTPException(
            status_code=429,
            detail="Too many messages. Please wait a moment before sending another.",
            headers={"Retry-After": str(max(retry_after, 1))},
        )

    bucket.append(now)

    # Opportunistic cleanup so idle sessions don't accumulate forever.
    if len(_RATE_BUCKETS) > 5000:
        for k in [k for k, v in _RATE_BUCKETS.items() if not v or v[-1] < window_start]:
            _RATE_BUCKETS.pop(k, None)


def invalidate_bot_cache(bot_id: Optional[str] = None) -> None:
    """Clears the resolved-bot cache after an admin edits bot configuration."""
    if bot_id:
        _BOT_CACHE.pop(bot_id, None)
    else:
        _BOT_CACHE.clear()
