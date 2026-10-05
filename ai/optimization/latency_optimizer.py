import time
import asyncio
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Any

@dataclass
class CachedSessionData:
    tenant_id: str
    bot_id: str
    business_name: str
    business_context: str
    booking_url: str
    voice_id: str
    call_direction: str  # 'inbound' | 'outbound'
    pre_warmed_chunks: List[Dict]
    warmed_at: float
    ttl_seconds: float = 300.0
    
    def is_valid(self) -> bool:
        """Returns True if cache is still within TTL."""
        return (time.monotonic() - self.warmed_at) < self.ttl_seconds

class VoiceSessionCache:
    def __init__(self, supabase_client, retriever, provider=None):
        self.supabase = supabase_client
        self.retriever = retriever
        self.provider = provider
        
    async def warm_up(self, tenant_id: str, bot_id: str, opening_utterance: str = '') -> CachedSessionData:
        """Called once at call start. Pre-fetches concurrently using asyncio.gather:
        1. Tenant bot config (business_name, context, booking_url, voice_id, call_direction)
           Uses the same synchronous Supabase client pattern as the rest of the codebase.
        2. Top-5 FAQ embeddings using opening_utterance (or 'services offered' if empty)
        Returns CachedSessionData dataclass.
        """
        query = opening_utterance if opening_utterance else "services offered"

        async def fetch_config() -> dict:
            """Wraps synchronous Supabase query in a coroutine for asyncio.gather compatibility."""
            try:
                loop = asyncio.get_running_loop()
                def _sync_fetch():
                    res = (
                        self.supabase.table("bots")
                        .select("business_name, business_context, booking_url, voice_id, call_direction")
                        .eq("id", bot_id)
                        .eq("tenant_id", tenant_id)
                        .execute()
                    )
                    return res.data[0] if res.data else {}
                return await loop.run_in_executor(None, _sync_fetch)
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(f"VoiceSessionCache.fetch_config failed: {e}")
                return {}

        async def fetch_faqs() -> list:
            """Retrieves top-5 FAQ chunks to pre-warm the call context."""
            try:
                result = await self.retriever.retrieve(query, tenant_id=tenant_id, bot_id=bot_id, top_k=5)
                if isinstance(result, list):
                    return result
                return result.get("chunks", []) if isinstance(result, dict) else []
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(f"VoiceSessionCache.fetch_faqs failed: {e}")
                return []

        config_data, faqs = await asyncio.gather(
            fetch_config(),
            fetch_faqs(),
        )

        return CachedSessionData(
            tenant_id=tenant_id,
            bot_id=bot_id,
            business_name=config_data.get("business_name", ""),
            business_context=config_data.get("business_context", ""),
            booking_url=config_data.get("booking_url", ""),
            voice_id=config_data.get("voice_id", ""),
            call_direction=config_data.get("call_direction", "inbound"),
            pre_warmed_chunks=faqs if isinstance(faqs, list) else [],
            warmed_at=time.monotonic(),
        )

class TurnTimer:
    def __init__(self, tracker: 'LatencyBudgetTracker', turn_id: str):
        self.tracker = tracker
        self.turn_id = turn_id
        self.start_time = 0.0
        
    def mark(self, stage: str):
        """Record a timestamp for a named stage."""
        pass # Additional custom stages could be tracked here
        
    def __enter__(self) -> 'TurnTimer':
        self.start_time = time.monotonic()
        return self
        
    def __exit__(self, *args):
        total_time = (time.monotonic() - self.start_time) * 1000.0
        self.tracker.record_turn_complete(self.turn_id, total_time)

class LatencyBudgetTracker:
    def __init__(self):
        self.turns: List[Dict] = []
    
    def start_turn(self, turn_id: str) -> TurnTimer:
        """Returns a context manager / timer object."""
        self.turns.append({"turn_id": turn_id})
        return TurnTimer(self, turn_id)
    
    def record_stt_final(self, turn_id: str, ms: float):
        """Record time from audio start to STT is_final."""
        self._record(turn_id, "stt_final", ms)
    
    def record_llm_first_token(self, turn_id: str, ms: float):
        """Record time from STT final to first LLM token."""
        self._record(turn_id, "llm_first_token", ms)
    
    def record_tts_first_chunk(self, turn_id: str, ms: float):
        """Record time from LLM first token to first TTS audio chunk."""
        self._record(turn_id, "tts_first_chunk", ms)
    
    def record_turn_complete(self, turn_id: str, ms: float):
        """Record total turn time."""
        self._record(turn_id, "turn_complete", ms)
        
    def _record(self, turn_id: str, metric: str, ms: float):
        for turn in reversed(self.turns):
            if turn.get("turn_id") == turn_id:
                turn[metric] = ms
                break
    
    def get_p90(self, metric: str) -> float:
        """Return P90 latency for a metric across all turns."""
        values = sorted([t[metric] for t in self.turns if metric in t])
        if not values:
            return 0.0
        idx = int(len(values) * 0.9)
        if idx >= len(values):
            idx = len(values) - 1
        return float(values[idx])
    
    def get_report(self) -> Dict:
        """Return full latency report with P50/P90/P99 for all metrics."""
        metrics = ["stt_final", "llm_first_token", "tts_first_chunk", "turn_complete"]
        report = {}
        for m in metrics:
            values = sorted([t[m] for t in self.turns if m in t])
            if not values:
                continue
            report[m] = {
                "p50": float(values[int(len(values) * 0.5)]),
                "p90": float(values[min(int(len(values) * 0.9), len(values) - 1)]),
                "p99": float(values[min(int(len(values) * 0.99), len(values) - 1)])
            }
        return report

def build_voice_token_budget() -> Dict:
    return {
        'system_prompt_max': 600,   # Tighter than chat (800)
        'history_turns_max': 6,     # 6 turns max (chat uses 10)
        'rag_chunks_max': 3,        # 3 chunks (chat uses 5)
        'rag_tokens_max': 800,      # 800 tokens (chat uses 1500)
        'response_max_tokens': 150, # Short spoken responses
    }
