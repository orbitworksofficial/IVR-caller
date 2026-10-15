"""
Outbound dial policy — the compliance gate in front of every outbound call.

Plan §06/§09: no outbound dial executes without a consent + DNC + calling-window
check *immediately before the dial*, enforced by the backend, never left to the
prompt. Everything here fails CLOSED: missing config, a bad timezone or a
database error blocks the dial.

`evaluate()` is pure (all facts passed in) so the rules are unit-testable;
`check_dial()` gathers the facts from the database and calls it.
"""

import logging
import re
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

E164 = re.compile(r"^\+[1-9]\d{6,14}$")

DEFAULT_WINDOW_START = "09:00:00"
DEFAULT_WINDOW_END = "18:00:00"
DEFAULT_TIMEZONE = "America/New_York"
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_RETRY_HOURS = 24


@dataclass(frozen=True)
class DialDecision:
    allowed: bool
    reason: str          # "ok" or the blocking reason
    detail: str = ""

    @property
    def attempt_outcome(self) -> Optional[str]:
        """dial_attempts.outcome for a blocked dial (schema: '<reason>_blocked')."""
        return None if self.allowed else f"{self.reason}_blocked"


def _parse_time(value: Any, default: str) -> time:
    return time.fromisoformat(str(value or default)[:8])


def in_calling_window(now_utc: datetime, start: Any, end: Any, tz_name: Optional[str]) -> bool:
    local = now_utc.astimezone(ZoneInfo(tz_name or DEFAULT_TIMEZONE)).time()
    s, e = _parse_time(start, DEFAULT_WINDOW_START), _parse_time(end, DEFAULT_WINDOW_END)
    if s <= e:
        return s <= local < e
    return local >= s or local < e  # window crosses midnight


def evaluate(
    *,
    phone_number: str,
    now_utc: datetime,
    on_dnc: bool,
    has_consent: bool,
    config: Dict[str, Any],
    previous_attempts: List[Dict[str, Any]],
    max_attempts: Optional[int] = None,
) -> DialDecision:
    """
    Rules, in order (first failure wins, so the logged reason is the most serious):
      invalid number → DNC → no consent → outside calling window
      → attempt limit reached → retry interval not yet elapsed
    `previous_attempts` are this number's real dials (blocked attempts excluded).
    """
    if not E164.match(phone_number or ""):
        return DialDecision(False, "invalid_number", f"{phone_number!r} is not E.164")
    if on_dnc:
        return DialDecision(False, "dnc", "number is on the tenant's do-not-call list")
    if not has_consent:
        return DialDecision(False, "consent", "no active call consent on record")

    try:
        if not in_calling_window(
            now_utc,
            config.get("calling_window_start"),
            config.get("calling_window_end"),
            config.get("calling_timezone"),
        ):
            return DialDecision(False, "window", "outside the tenant's calling window")
    except Exception as e:
        return DialDecision(False, "window", f"calling window misconfigured: {e}")

    limit = max_attempts or config.get("max_call_attempts") or DEFAULT_MAX_ATTEMPTS
    if len(previous_attempts) >= int(limit):
        return DialDecision(False, "attempts", f"{len(previous_attempts)} of {limit} attempts used")

    retry_hours = config.get("retry_interval_hours") or DEFAULT_RETRY_HOURS
    dialed = [a["dialed_at"] for a in previous_attempts if a.get("dialed_at")]
    if dialed:
        last = max(datetime.fromisoformat(str(d).replace("Z", "+00:00")) for d in dialed)
        if now_utc - last < timedelta(hours=float(retry_hours)):
            return DialDecision(False, "retry", f"last dialed {last.isoformat()}, retry after {retry_hours}h")

    return DialDecision(True, "ok")


async def check_dial(
    store: Any,
    tenant_id: str,
    bot_id: str,
    phone_number: str,
    campaign: Optional[Dict[str, Any]] = None,
    now_utc: Optional[datetime] = None,
) -> DialDecision:
    """Gathers compliance facts from the DB and evaluates them. Fails closed."""
    try:
        config = await store.load_ivr_config(tenant_id, bot_id)
        on_dnc = await store.is_on_dnc(tenant_id, phone_number)
        has_consent = await store.has_active_consent(tenant_id, phone_number, "call")
        previous = await store.list_dial_attempts(
            tenant_id, phone_number, campaign.get("id") if campaign else None
        )
    except Exception as e:
        logger.error(f"Dial policy lookup failed for tenant {tenant_id}: {e}")
        return DialDecision(False, "check_error", "could not verify compliance; blocking for safety")

    return evaluate(
        phone_number=phone_number,
        now_utc=now_utc or datetime.now(timezone.utc),
        on_dnc=on_dnc,
        has_consent=has_consent,
        config=config,
        previous_attempts=previous,
        max_attempts=(campaign or {}).get("max_attempts"),
    )
