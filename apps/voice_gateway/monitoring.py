"""
Voice Gateway Monitoring, Alerting & SLO Breach Tracking (Phase 3 — Kashif).

Tracks in-memory metrics and dispatches structured alerts when:
  1. A live call drops unexpectedly
  2. Deepgram STT or ElevenLabs TTS encounters a runtime error
  3. Turn latency exceeds the P90 SLO target (default 1000 ms)

Integrates with Sentry when `SENTRY_DSN` is set, and optionally posts JSON
alert payloads to `VOICE_ALERT_WEBHOOK_URL` (Slack/PagerDuty/ops webhook).
"""

import asyncio
import collections
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Deque, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

DEFAULT_SLO_P90_MS = 1000
MAX_LATENCY_SAMPLES = 500
MAX_RECENT_ALERTS = 100


class VoiceMonitor:
    def __init__(self, slo_p90_ms: Optional[int] = None):
        self.slo_p90_ms = slo_p90_ms or int(os.getenv("VOICE_LATENCY_SLO_MS", str(DEFAULT_SLO_P90_MS)))
        self.active_calls: int = 0
        self.total_calls: int = 0
        self.dropped_calls: int = 0
        self.stt_failures: int = 0
        self.tts_failures: int = 0
        self.slo_breaches: int = 0
        self.rate_limited_requests: int = 0
        self._latencies_ms: Deque[int] = collections.deque(maxlen=MAX_LATENCY_SAMPLES)
        self._alerts: Deque[Dict[str, Any]] = collections.deque(maxlen=MAX_RECENT_ALERTS)
        self._sentry_initialized = False
        self._init_sentry()

    def _init_sentry(self) -> None:
        dsn = os.getenv("SENTRY_DSN", "").strip()
        if not dsn:
            return
        try:
            import sentry_sdk  # type: ignore
            sentry_sdk.init(dsn=dsn, traces_sample_rate=0.2)
            self._sentry_initialized = True
            logger.info("VoiceMonitor: Sentry initialized.")
        except Exception as e:
            logger.warning(f"VoiceMonitor: Sentry initialization skipped: {e}")

    def call_started(self, call_id: str, tenant_id: str) -> None:
        self.active_calls += 1
        self.total_calls += 1

    def call_ended(self, call_id: str, dropped: bool = False, reason: str = "") -> None:
        self.active_calls = max(0, self.active_calls - 1)
        if dropped:
            self.dropped_calls += 1
            self._emit_alert(
                kind="call_dropped",
                severity="warning",
                message=f"Call {call_id} dropped unexpectedly: {reason or 'connection lost'}",
                metadata={"call_id": call_id, "reason": reason},
            )

    def record_stt_failure(self, call_id: str, error: str) -> None:
        self.stt_failures += 1
        self._emit_alert(
            kind="stt_failure",
            severity="error",
            message=f"STT failure on call {call_id}: {error}",
            metadata={"call_id": call_id, "error": error},
        )

    def record_tts_failure(self, call_id: str, error: str) -> None:
        self.tts_failures += 1
        self._emit_alert(
            kind="tts_failure",
            severity="error",
            message=f"TTS failure on call {call_id}: {error}",
            metadata={"call_id": call_id, "error": error},
        )

    def record_turn_latency(self, call_id: str, latency_ms: int) -> None:
        self._latencies_ms.append(latency_ms)
        if latency_ms > self.slo_p90_ms:
            self.slo_breaches += 1
            self._emit_alert(
                kind="latency_slo_breach",
                severity="warning",
                message=(
                    f"Call {call_id} turn latency {latency_ms}ms exceeded "
                    f"SLO target ({self.slo_p90_ms}ms)"
                ),
                metadata={
                    "call_id": call_id,
                    "latency_ms": latency_ms,
                    "slo_p90_ms": self.slo_p90_ms,
                },
            )

    def record_rate_limited(self, key: str) -> None:
        self.rate_limited_requests += 1

    def latency_p50(self) -> int:
        if not self._latencies_ms:
            return 0
        s = sorted(self._latencies_ms)
        return s[len(s) // 2]

    def latency_p90(self) -> int:
        if not self._latencies_ms:
            return 0
        s = sorted(self._latencies_ms)
        idx = min(len(s) - 1, int(round(0.9 * (len(s) - 1))))
        return s[idx]

    def snapshot(self) -> Dict[str, Any]:
        p90 = self.latency_p90()
        return {
            "active_calls": self.active_calls,
            "total_calls": self.total_calls,
            "dropped_calls": self.dropped_calls,
            "stt_failures": self.stt_failures,
            "tts_failures": self.tts_failures,
            "slo_breaches": self.slo_breaches,
            "rate_limited_requests": self.rate_limited_requests,
            "latency_samples": len(self._latencies_ms),
            "latency_p50_ms": self.latency_p50(),
            "latency_p90_ms": p90,
            "slo_target_p90_ms": self.slo_p90_ms,
            "slo_healthy": p90 <= self.slo_p90_ms,
            "recent_alerts": list(self._alerts)[-20:],
        }

    def _emit_alert(
        self,
        kind: str,
        severity: str,
        message: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        alert = {
            "kind": kind,
            "severity": severity,
            "message": message,
            "metadata": metadata or {},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self._alerts.append(alert)
        if severity == "error":
            logger.error(f"VOICE ALERT [{kind}]: {message}")
        else:
            logger.warning(f"VOICE ALERT [{kind}]: {message}")

        if self._sentry_initialized:
            try:
                import sentry_sdk  # type: ignore
                sentry_sdk.capture_message(f"[{kind}] {message}", level=severity)
            except Exception:
                pass

        webhook_url = os.getenv("VOICE_ALERT_WEBHOOK_URL", "").strip()
        if webhook_url:
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self._post_webhook(webhook_url, alert))
            except RuntimeError:
                pass

    async def _post_webhook(self, url: str, alert: Dict[str, Any]) -> None:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                await client.post(url, json=alert)
        except Exception as e:
            logger.debug(f"VoiceMonitor webhook delivery failed: {e}")


class SlidingWindowRateLimiter:
    """In-memory per-key sliding window rate limiter for Voice Gateway endpoints."""

    def __init__(self, max_requests: int = 60, window_seconds: float = 60.0):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._hits: Dict[str, Deque[float]] = collections.defaultdict(collections.deque)

    def allow(self, key: str, now: Optional[float] = None) -> bool:
        t = now if now is not None else time.monotonic()
        cutoff = t - self.window_seconds
        q = self._hits[key]
        while q and q[0] <= cutoff:
            q.popleft()
        if len(q) >= self.max_requests:
            return False
        q.append(t)
        return True

    def reset(self) -> None:
        self._hits.clear()
