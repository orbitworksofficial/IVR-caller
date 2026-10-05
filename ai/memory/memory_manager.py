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

    # ── Call / Voice-specific memory methods ─────────────────────────────────

    CALL_SUMMARY_PROMPT = """
Summarize the following phone call transcript into a compact briefing note.
Focus on: caller's name, company, what they wanted, which service(s) they were interested in,
any contact details shared, objections raised, and the call outcome.
Be extremely concise — max 4 sentences. Write in past tense, third person.

Call transcript:
{transcript}

Return only the summary text. No preamble.
"""

    CALL_DISPOSITION_PROMPT = """
Read the following phone call transcript and determine the call outcome.

Return ONLY one of these exact values (no explanation):
booked | qualified | declined | transferred | voicemail | dropped | callback_requested | resolved

Call transcript:
{transcript}
"""

    async def extract_call_summary(
        self,
        turns: list,
        provider_instance: Any,
    ) -> str:
        """
        Generates a compact CRM-ready summary from call turn transcripts.
        Used to populate calls.call_summary and as the handoff briefing.

        Args:
            turns: List of {'speaker': 'caller'|'agent', 'text': str} dicts
            provider_instance: The LLM provider (shared with chat)

        Returns:
            Concise 3-4 sentence call summary string
        """
        if not turns:
            return ""

        transcript = "\n".join(
            f"{t['speaker'].upper()}: {t['text']}" for t in turns
        )
        prompt = self.CALL_SUMMARY_PROMPT.format(transcript=transcript)

        try:
            response = await provider_instance.generate(
                messages=[{"role": "user", "content": prompt}],
                model="gpt-5.6-luna",
                temperature=0.2,
                max_tokens=200,
            )
            return response.get("text", "").strip()
        except Exception as e:
            logger.warning(f"extract_call_summary: LLM call failed: {e}")
            return ""

    async def get_call_disposition(
        self,
        turns: list,
        provider_instance: Any,
    ) -> str:
        """
        Derives the call outcome from the transcript using the LLM.

        Returns one of:
            'booked' | 'qualified' | 'declined' | 'transferred' |
            'voicemail' | 'dropped' | 'callback_requested' | 'resolved'
        """
        valid = {
            "booked", "qualified", "declined", "transferred",
            "voicemail", "dropped", "callback_requested", "resolved"
        }
        if not turns:
            return "dropped"

        transcript = "\n".join(
            f"{t['speaker'].upper()}: {t['text']}" for t in turns
        )
        prompt = self.CALL_DISPOSITION_PROMPT.format(transcript=transcript)

        try:
            response = await provider_instance.generate(
                messages=[{"role": "user", "content": prompt}],
                model="gpt-5.6-luna",
                temperature=0.0,
                max_tokens=10,
            )
            outcome = response.get("text", "resolved").strip().lower()
            return outcome if outcome in valid else "resolved"
        except Exception as e:
            logger.warning(f"get_call_disposition: LLM call failed: {e}")
            return "resolved"

    def split_call_turns(
        self,
        turns: list,
        recent_limit: int = 8,
    ) -> Dict[str, Any]:
        """
        Splits call turns into recent (kept in prompt) and older (summarized).
        Phone calls use a tighter window than chat — 6-10 turns instead of 10+.

        Args:
            turns: List of {'speaker': str, 'text': str} dicts
            recent_limit: Number of recent turns to keep verbatim (default 8)

        Returns:
            Dict with 'recent': List and 'to_summarize': List
        """
        if len(turns) <= recent_limit:
            return {"recent": turns, "to_summarize": []}
        split_point = len(turns) - recent_limit
        return {
            "recent": turns[split_point:],
            "to_summarize": turns[:split_point],
        }

    def build_call_context_block(
        self,
        call_summary: Optional[str] = None,
        structured_memory: Optional[Dict[str, Any]] = None,
        call_direction: str = "inbound",
        call_reason: str = "",
        consent_memory: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Compiles the voice-mode memory context block injected into the call prompt.
        Tighter than the chat version — optimized for short in-call context.

        Args:
            call_summary: Rolling summary of earlier turns in this call
            structured_memory: Extracted caller profile (name, company, etc.)
            call_direction: 'inbound' | 'outbound'
            call_reason: For outbound — why we placed this call
            consent_memory: Contact preferences from prior interactions

        Returns:
            Compact context string for injection into voice system prompt
        """
        parts = []

        if call_direction == "outbound" and call_reason:
            parts.append(f"Reason for this call: {call_reason}")

        if call_summary:
            parts.append(f"Earlier in this call: {call_summary}")

        if structured_memory:
            known = []
            if structured_memory.get("name"):
                known.append(f"Caller: {structured_memory['name']}")
            if structured_memory.get("company"):
                known.append(f"Company: {structured_memory['company']}")
            if structured_memory.get("services_of_interest"):
                services = ", ".join(structured_memory["services_of_interest"])
                known.append(f"Interested in: {services}")
            if structured_memory.get("lead_stage"):
                known.append(f"Stage: {structured_memory['lead_stage']}")
            if known:
                parts.append("Caller info: " + " | ".join(known))

        if consent_memory:
            prefs = []
            if consent_memory.get("recording_consent"):
                prefs.append("consented to recording")
            preferred_channel = consent_memory.get("preferred_channel")
            if preferred_channel:
                prefs.append(f"prefers follow-up by {preferred_channel}")
            if prefs:
                parts.append("Consent/preferences: " + ", ".join(prefs))

        return "\n".join(parts) if parts else ""
