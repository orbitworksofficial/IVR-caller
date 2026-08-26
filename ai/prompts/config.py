from typing import Dict, Any
from supabase import Client
from ai.prompts.templates import compile_system_prompt

class TenantConfigResolver:
    """Fetches dynamic client/bot configuration settings and resolves context prompts."""

    def __init__(self, supabase_client: Client):
        self.supabase = supabase_client

    async def get_bot_config(self, bot_id: str) -> Dict[str, Any]:
        """
        Loads the bot and tenant configuration variables from Supabase.
        """
        response = self.supabase.table("bots").select(
            "id, tenant_id, name, support_mode_enabled, sales_mode_enabled, welcome_message, system_personality, booking_url, handoff_settings"
        ).eq("id", bot_id).execute()
        
        if not response.data:
            raise ValueError(f"Configuration Error: Bot configuration not found for ID '{bot_id}'.")
            
        return response.data[0]

    async def resolve_prompt(self, bot_id: str, retrieved_knowledge: str) -> str:
        """
        Fetches configurations and returns a compiled system prompt with injection-safe variables.
        """
        config = await self.get_bot_config(bot_id)
        
        # Load associated business context or defaults if not present
        personality = config.get("system_personality") or "A helpful customer assistant."
        business_name = config.get("name") or "Our Company"
        support_mode = config.get("support_mode_enabled", True)
        sales_mode = config.get("sales_mode_enabled", True)

        return compile_system_prompt(
            business_name=business_name,
            business_context=personality,
            retrieved_knowledge=retrieved_knowledge,
            support_mode_enabled=support_mode,
            sales_mode_enabled=sales_mode
        )
