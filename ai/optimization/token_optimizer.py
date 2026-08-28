"""
Token Optimizer — Phase 3
Counts, trims, and compresses token usage per request.
Keeps costs measurable and context windows under control.

Uses character-based estimation (3.5 chars ≈ 1 token) when tiktoken
is unavailable, which is accurate enough for planning/logging purposes.
"""

import re
import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Token budget constants (safe limits for gpt-5.6-luna context window)
MAX_HISTORY_TOKENS = 2000       # Max tokens for conversation history
MAX_KNOWLEDGE_TOKENS = 1500     # Max tokens for retrieved knowledge chunks
MAX_SYSTEM_PROMPT_TOKENS = 800  # Max tokens for system prompt
CHARS_PER_TOKEN = 3.5           # Conservative approximation


class TokenOptimizer:
    """
    Manages token budgets across system prompt, knowledge context,
    and conversation history to keep costs predictable and low.
    """

    def __init__(
        self,
        max_history_tokens: int = MAX_HISTORY_TOKENS,
        max_knowledge_tokens: int = MAX_KNOWLEDGE_TOKENS,
    ):
        self.max_history_tokens = max_history_tokens
        self.max_knowledge_tokens = max_knowledge_tokens
        self._tiktoken_enc = None

    def _load_tiktoken(self):
        """Lazily load tiktoken encoder if available."""
        if self._tiktoken_enc is not None:
            return True
        try:
            import tiktoken
            self._tiktoken_enc = tiktoken.get_encoding("cl100k_base")
            return True
        except ImportError:
            return False

    def count_tokens(self, text: str) -> int:
        """
        Estimates token count for a string.
        Uses tiktoken if available, falls back to char-based approximation.
        """
        if self._load_tiktoken():
            return len(self._tiktoken_enc.encode(text))
        return max(1, int(len(text) / CHARS_PER_TOKEN))

    def count_messages_tokens(self, messages: List[Dict[str, str]]) -> int:
        """Counts total estimated tokens across a list of messages."""
        total = 0
        for msg in messages:
            total += self.count_tokens(msg.get("content", ""))
            total += 4  # overhead per message (role, formatting)
        return total

    def trim_history(
        self,
        messages: List[Dict[str, str]],
        budget: Optional[int] = None,
    ) -> List[Dict[str, str]]:
        """
        Trims conversation history to fit within token budget.
        Always keeps the most recent messages; drops oldest first.
        Never drops system messages.
        """
        budget = budget or self.max_history_tokens
        system_msgs = [m for m in messages if m.get("role") == "system"]
        chat_msgs = [m for m in messages if m.get("role") != "system"]

        # Start from most recent and work backwards
        kept = []
        token_count = 0
        for msg in reversed(chat_msgs):
            msg_tokens = self.count_tokens(msg.get("content", "")) + 4
            if token_count + msg_tokens > budget:
                break
            kept.insert(0, msg)
            token_count += msg_tokens

        if len(kept) < len(chat_msgs):
            logger.info(
                f"TokenOptimizer: trimmed history from {len(chat_msgs)} → {len(kept)} messages "
                f"({token_count} tokens within {budget} budget)"
            )

        return system_msgs + kept

    def compress_knowledge(
        self,
        chunks: List[Dict[str, Any]],
        budget: Optional[int] = None,
    ) -> Tuple[List[Dict[str, Any]], int]:
        """
        Deduplicates and trims retrieved chunks to fit within the knowledge token budget.
        - Removes chunks whose content overlaps heavily with a higher-ranked chunk
        - Truncates individual chunks that are too long

        Returns (compressed_chunks, total_tokens_used)
        """
        budget = budget or self.max_knowledge_tokens
        seen_fingerprints = set()
        result = []
        total_tokens = 0

        for chunk in chunks:
            content = chunk.get("content", "")

            # Fingerprint: first 80 chars normalized (catches near-duplicates from overlapping windows)
            fingerprint = re.sub(r"\s+", " ", content[:80]).strip().lower()
            if fingerprint in seen_fingerprints:
                continue
            seen_fingerprints.add(fingerprint)

            chunk_tokens = self.count_tokens(content)

            # If single chunk exceeds budget, truncate it
            if chunk_tokens > budget:
                max_chars = int(budget * CHARS_PER_TOKEN)
                content = content[:max_chars] + "..."
                chunk = {**chunk, "content": content}
                chunk_tokens = budget

            if total_tokens + chunk_tokens > budget:
                break

            result.append(chunk)
            total_tokens += chunk_tokens

        return result, total_tokens

    def build_usage_report(
        self,
        system_prompt: str,
        history: List[Dict[str, str]],
        knowledge_chunks: List[Dict[str, Any]],
    ) -> Dict[str, int]:
        """
        Estimates full token breakdown for a request before sending to the API.
        Used for pre-request logging in the /chat endpoint.
        """
        knowledge_text = " ".join(c.get("content", "") for c in knowledge_chunks)
        return {
            "system_tokens": self.count_tokens(system_prompt),
            "history_tokens": self.count_messages_tokens(history),
            "knowledge_tokens": self.count_tokens(knowledge_text),
            "total_estimated": (
                self.count_tokens(system_prompt)
                + self.count_messages_tokens(history)
                + self.count_tokens(knowledge_text)
            ),
        }
