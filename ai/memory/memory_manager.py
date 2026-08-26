import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class MemoryManager:
    """
    Manages conversational memory, context compression, and structured user memory.

    Phase 2 additions:
    - extract_structured_memory(): pulls name/company/interest from conversation
    - build_context_block(): compiles recent messages + summary + structured memory
      into a single clean context string for the system prompt
    """

    def __init__(self, recent_limit: int = 10):
        self.recent_limit = recent_limit

    def split_history(self, messages: List[Dict[str, str]]) -> Dict[str, Any]:
        """
        Splits chat history into recent messages (kept intact) and
        older messages (candidates for summarization).
        """
        chat_messages = [m for m in messages if m.get("role") != "system"]

        if len(chat_messages) <= self.recent_limit:
            return {"recent": chat_messages, "to_summarize": []}

        split_point = len(chat_messages) - self.recent_limit
        return {
            "recent": chat_messages[split_point:],
            "to_summarize": chat_messages[:split_point],
        }

    async def generate_summary(
        self,
        old_messages: List[Dict[str, str]],
        provider_instance: Any,
        current_summary: str = "",
    ) -> str:
        """
        Calls the LLM to compress older conversation turns into a concise summary.
        Uses the summary prompt from templates.
        """
        if not old_messages:
            return current_summary

        from ai.prompts.templates import compile_summary_prompt

        formatted = "\n".join(
            [f"{m['role'].upper()}: {m['content']}" for m in old_messages]
        )
        prompt = compile_summary_prompt(
            new_turns=formatted,
            existing_summary=current_summary,
        )
        response = await provider_instance.generate(
            messages=[{"role": "user", "content": prompt}],
            model="gpt-5.6-luna",
            temperature=0.3,
            max_tokens=300,
        )
        return response.get("text", current_summary).strip()

    async def extract_structured_memory(
        self,
        messages: List[Dict[str, str]],
        provider_instance: Any,
    ) -> Dict[str, Any]:
        """
        Runs a structured extraction pass over the conversation to pull out
        qualification fields: name, email, company, industry, services of interest,
        budget signal, urgency, and current lead stage.

        Returns a dict of extracted fields (nulls for anything not mentioned).
        """
        from ai.prompts.templates import compile_qualification_prompt

        conversation_text = "\n".join(
            [f"{m['role'].upper()}: {m['content']}" for m in messages
             if m.get("role") in ("user", "assistant")]
        )

        prompt = compile_qualification_prompt(conversation_text)
        response = await provider_instance.generate(
            messages=[{"role": "user", "content": prompt}],
            model="gpt-5.6-luna",
            temperature=0.0,
            max_tokens=400,
        )

        raw = response.get("text", "{}").strip()
        # Strip markdown code fences if model wraps output
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]

        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Failed to parse structured memory JSON. Returning empty.")
            return {}

    def build_context_block(
        self,
        structured_memory: Optional[Dict[str, Any]] = None,
        conversation_summary: Optional[str] = None,
    ) -> str:
        """
        Compiles a readable memory context block to inject into the sales prompt.
        Combines the structured memory fields with the conversation summary.
        """
        parts = []

        if conversation_summary:
            parts.append(f"Conversation summary:\n{conversation_summary}")

        if structured_memory:
            fields = []
            if structured_memory.get("name"):
                fields.append(f"Name: {structured_memory['name']}")
            if structured_memory.get("company"):
                fields.append(f"Company: {structured_memory['company']}")
            if structured_memory.get("industry"):
                fields.append(f"Industry: {structured_memory['industry']}")
            if structured_memory.get("problem_statement"):
                fields.append(f"Problem: {structured_memory['problem_statement']}")
            if structured_memory.get("services_of_interest"):
                services = ", ".join(structured_memory["services_of_interest"])
                fields.append(f"Interested in: {services}")
            if structured_memory.get("lead_stage"):
                fields.append(f"Lead stage: {structured_memory['lead_stage']}")

            if fields:
                parts.append("Known about this visitor:\n" + "\n".join(f"- {f}" for f in fields))

        return "\n\n".join(parts) if parts else ""
