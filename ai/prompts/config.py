"""
Enhanced TenantConfigResolver — Phase 4
Supports per-tenant config variables: tone, language, topic restrictions,
custom escalation contacts, and behavior overrides — all from the DB.
"""

import logging
from typing import Any, Dict, List, Optional
from supabase import Client
from ai.prompts.templates import compile_system_prompt

logger = logging.getLogger(__name__)

# Default config applied when a bot doesn't override a setting
DEFAULT_CONFIG = {
    "tone": "professional",              # professional | friendly | formal
    "response_language": "english",
    "max_response_length": "medium",     # short | medium | long
    "topic_restrictions": [],            # topics the bot must not discuss
    "escalation_email": None,
    "escalation_phone": None,
    "business_hours": "Monday–Friday, 9AM–6PM PKT",
    "support_mode_enabled": True,
    "sales_mode_enabled": True,
    "booking_url": None,
    "welcome_message": "Hi! I'm here to help. What can I do for you today?",
}


class TenantConfigResolver:
    """
    Phase 4: Fetches and resolves per-tenant bot configuration.
    All AI behavior is driven by the DB — no code changes needed for new tenants.
    """

    def __init__(self, supabase_client: Client):
        self.supabase = supabase_client
        self._cache: Dict[str, Dict[str, Any]] = {}  # simple in-process cache

    async def get_bot_config(self, bot_id: str) -> Dict[str, Any]:
        """
        Loads the bot and tenant configuration from Supabase.
        Merges DB values over DEFAULT_CONFIG so missing fields always have a safe default.
        Results are cached per bot_id for the lifetime of this request handler instance.
        """
        if bot_id in self._cache:
            return self._cache[bot_id]

        response = self.supabase.table("bots").select(
            "id, tenant_id, name, support_mode_enabled, sales_mode_enabled, "
            "welcome_message, system_personality, booking_url, handoff_settings, "
            "tone, response_language, max_response_length, topic_restrictions, "
            "escalation_email, escalation_phone, business_hours"
        ).eq("id", bot_id).execute()

        if not response.data:
            raise ValueError(f"Bot config not found for ID '{bot_id}'.")

        raw = response.data[0]

        # Merge over defaults — DB values win
        config = {**DEFAULT_CONFIG, **{k: v for k, v in raw.items() if v is not None}}
        self._cache[bot_id] = config
        return config

    async def resolve_prompt(self, bot_id: str, retrieved_knowledge: str,
                              memory_context: str = "") -> str:
        """
        Fetches tenant config and compiles the system prompt with all
        tenant-specific variables injected.
        """
        config = await self.get_bot_config(bot_id)
        personality = config.get("system_personality") or "A helpful customer assistant."

        # Phase 4: append tenant-specific behavior overrides to personality
        personality = self._apply_config_overrides(personality, config)

        return compile_system_prompt(
            business_name=config.get("name", "Our Company"),
            business_context=personality,
            retrieved_knowledge=retrieved_knowledge,
            support_mode_enabled=config.get("support_mode_enabled", True),
            sales_mode_enabled=config.get("sales_mode_enabled", True),
            memory_context=memory_context,
        )

    def _apply_config_overrides(self, base_personality: str, config: Dict[str, Any]) -> str:
        """
        Appends tenant-specific behavior rules to the personality string.
        These are injected into the system prompt at compile time.
        """
        addons = []

        tone = config.get("tone", "professional")
        if tone == "friendly":
            addons.append("Use a warm, conversational and friendly tone.")
        elif tone == "formal":
            addons.append("Maintain a strictly formal and professional tone at all times.")

        lang = config.get("response_language", "english")
        if lang != "english":
            addons.append(f"Always respond in {lang}.")

        length = config.get("max_response_length", "medium")
        if length == "short":
            addons.append("Keep responses concise — no more than 3 short paragraphs.")
        elif length == "long":
            addons.append("Provide detailed, comprehensive responses.")

        restrictions: List[str] = config.get("topic_restrictions") or []
        if restrictions:
            joined = ", ".join(restrictions)
            addons.append(f"Do NOT discuss the following topics: {joined}.")

        hours = config.get("business_hours")
        if hours:
            addons.append(f"Business hours: {hours}.")

        esc_email = config.get("escalation_email")
        esc_phone = config.get("escalation_phone")
        if esc_email or esc_phone:
            contact = " | ".join(filter(None, [esc_email, esc_phone]))
            addons.append(f"For urgent matters, direct users to: {contact}.")

        if addons:
            return base_personality + "\n\n" + "\n".join(addons)
        return base_personality

    async def get_tenant_usage_summary(
        self, tenant_id: str, limit: int = 30
    ) -> Dict[str, Any]:
        """
        Phase 4: Aggregates usage and cost metrics for a tenant.
        Used by the /usage endpoint for cost monitoring.
        """
        resp = self.supabase.table("usage_events").select(
            "model, input_tokens, output_tokens, cost_usd, latency_ms, created_at"
        ).eq("tenant_id", tenant_id).order(
            "created_at", desc=True
        ).limit(limit).execute()

        events = resp.data or []
        if not events:
            return {"events": 0, "total_cost_usd": 0.0, "avg_latency_ms": 0}

        total_cost = sum(float(e.get("cost_usd", 0) or 0) for e in events)
        total_latency = sum(int(e.get("latency_ms", 0) or 0) for e in events)
        input_tokens = sum(int(e.get("input_tokens", 0) or 0) for e in events)
        output_tokens = sum(int(e.get("output_tokens", 0) or 0) for e in events)

        model_counts: Dict[str, int] = {}
        for e in events:
            m = e.get("model", "unknown")
            model_counts[m] = model_counts.get(m, 0) + 1

        return {
            "events": len(events),
            "total_cost_usd": round(total_cost, 6),
            "avg_latency_ms": round(total_latency / len(events)),
            "total_input_tokens": input_tokens,
            "total_output_tokens": output_tokens,
            "model_distribution": model_counts,
            "most_recent": events[0].get("created_at"),
        }
