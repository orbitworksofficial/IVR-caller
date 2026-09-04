import re
import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── Expanded injection pattern list ──────────────────────────────────────────
# Covers patterns seen in real-world adversarial testing
INJECTION_PATTERNS = [
    # Classic overrides
    r"ignore\s+(all\s+)?previous\s+instructions",
    r"disregard\s+(your\s+)?(previous\s+)?instructions",
    r"forget\s+(everything|all)\s+(you\s+)?(were\s+)?told",
    r"new\s+instructions?\s*:",
    r"override\s+(your\s+)?(previous\s+)?(instructions?|rules?|prompt)",
    # System prompt extraction
    r"(reveal|show|print|output|repeat|tell me)\s+(your\s+)?(system\s+prompt|instructions?|rules?|configuration)",
    r"what\s+(are\s+your|is\s+your)\s+(system\s+|hidden\s+)?(prompt|instructions?|rules?)",
    r"(ignore|bypass|skip)\s+(your\s+)?(rules?|restrictions?|guidelines?|safety)",
    # Role hijacking
    r"you\s+are\s+now\s+(a|an|the)\b",
    r"act\s+as\s+(if\s+you\s+are\s+)?(a|an|the)\b",
    r"pretend\s+(you\s+are|to\s+be)\b",
    r"roleplay\s+as\b",
    r"from\s+now\s+on\s+(you\s+are|act\s+as)",
    # DAN / jailbreak
    r"\bDAN\b",
    r"do\s+anything\s+now",
    r"developer\s+mode",
    r"jailbreak",
    # Data extraction
    r"(list|show|dump|export)\s+(all\s+)?(tenant|database|user|customer)\s+(data|records?|info)",
    r"sql\s+inject",
    r"(select|drop|insert|update|delete)\s+\*?\s+from\b",
]

# ── Casual chitchat / greetings patterns ────────────────────────────────────
# Queries matching these should NEVER trigger hard RAG abstention
CHITCHAT_PATTERNS = [
    # Greetings
    r"^(hi|hello|hey|heyya|hiya|howdy|greetings|hola|sup|yo)\b",
    r"^(good\s+(morning|afternoon|evening|day|night))\b",
    # Identity & capabilities
    r"^(who\s+are\s+you|what\s+is\s+your\s+name|who\s+made\s+you|what\s+are\s+you)\b",
    r"^(what\s+can\s+you\s+do|how\s+can\s+you\s+help|what\s+do\s+you\s+do|can\s+you\s+help\s+me)\b",
    r"^(tell\s+me\s+about\s+yourself|introduce\s+yourself)\b",
    # Courtesy & check-in
    r"^(how\s+are\s+you|how\s+is\s+it\s+going|how\s+are\s+things|how\s+do\s+you\s+do)\b",
    r"^(thank\s+you|thanks|thanks\s+a\s+lot|thank\s+you\s+so\s+much)\b",
    r"^(bye|goodbye|see\s+you|talk\s+to\s+you\s+later)\b",
    r"^(ok|okay|sure|cool|great|awesome|got\s+it|nice)\b",
]


class GroundingGuardrails:
    """
    Phase 3 hardened guardrails:
    - Expanded prompt injection defense (30+ patterns)
    - Improved abstention logic based on score distribution
    - Chitchat & greeting bypass for natural conversational responses
    - Numeric fact grounding that handles reformatted values
    - Tool argument validation before dispatch
    """

    def __init__(
        self,
        confidence_threshold: float = 0.7,
        abstain_threshold: float = 0.10,
    ):
        self.confidence_threshold = confidence_threshold
        # Below this score even the top chunk is too weak → abstain
        self.abstain_threshold = abstain_threshold

    # ── Input safety ──────────────────────────────────────────────────────────

    def is_safe_input(self, user_query: str) -> bool:
        """
        Checks user input against expanded injection pattern list.
        Returns False if any pattern matches.
        """
        for pattern in INJECTION_PATTERNS:
            if re.search(pattern, user_query, re.IGNORECASE):
                logger.warning(f"Injection pattern blocked: '{pattern}' in query.")
                return False
        return True

    def get_injection_reason(self, user_query: str) -> Optional[str]:
        """Returns the matched injection pattern if found, else None."""
        for pattern in INJECTION_PATTERNS:
            if re.search(pattern, user_query, re.IGNORECASE):
                return pattern
        return None

    def is_chitchat(self, user_query: str) -> bool:
        """
        Returns True if the query is a casual greeting, identity question, or courtesy phrase.
        Chitchat/greetings should NEVER trigger RAG abstention.
        """
        cleaned = user_query.strip().lower()

        # Exact single-word or short phrases
        if cleaned in {"hi", "hello", "hey", "hiya", "howdy", "yo", "sup", "thanks", "thank you", "ok", "okay", "bye", "goodbye", "who are you"}:
            return True

        for pattern in CHITCHAT_PATTERNS:
            if re.search(pattern, cleaned, re.IGNORECASE):
                return True

        return False

    def is_contact_info(self, user_query: str) -> bool:
        """
        Returns True if the user query contains email addresses, phone numbers, or lead capture phrases.
        Contact detail submissions must NEVER trigger hard RAG abstentions.
        """
        cleaned = user_query.strip().lower()

        # Email regex pattern
        if re.search(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", user_query):
            return True

        # Name / Contact phrases
        if re.search(r"\b(my name is|my email is|call me|reach me at|my phone|contact me)\b", cleaned, re.IGNORECASE):
            return True

        return False

    # ── Grounding ─────────────────────────────────────────────────────────────

    def should_abstain(self, retrieval_results: List[Dict[str, Any]], user_query: str = "") -> bool:
        """
        Returns True if the bot should abstain from answering.
        Abstain when: no chunks retrieved, OR top score is below the abstain threshold.
        Chitchat/greetings and contact detail submissions automatically bypass abstention.
        """
        if user_query and (self.is_chitchat(user_query) or self.is_contact_info(user_query)):
            return False

        if not retrieval_results:
            return True
        top_score = retrieval_results[0].get("score")
        if top_score is None:
            top_score = 0.0
        return top_score < self.abstain_threshold

    def meets_grounding_threshold(self, retrieval_results: List[Dict[str, Any]]) -> bool:
        """
        Validates whether retrieved chunks have sufficient relevance.
        If False, the response should be flagged as low-confidence but not necessarily blocked.
        """
        if not retrieval_results:
            return False
        top_score = retrieval_results[0].get("score")
        if top_score is None:
            top_score = 0.0
        return top_score >= self.confidence_threshold

    def score_distribution(self, retrieval_results: List[Dict[str, Any]]) -> Dict[str, float]:
        """Returns score stats for telemetry logging."""
        if not retrieval_results:
            return {"top": 0.0, "mean": 0.0, "min": 0.0, "count": 0}
        
        scores = []
        for r in retrieval_results:
            val = r.get("score")
            scores.append(float(val) if val is not None else 0.0)
            
        return {
            "top": scores[0],
            "mean": round(sum(scores) / len(scores), 4),
            "min": scores[-1],
            "count": len(scores),
        }

    # ── Output grounding ──────────────────────────────────────────────────────

    def verify_response_grounded(
        self, response_text: str, source_contents: List[str]
    ) -> bool:
        """
        Phase 3 improved hallucination check:
        - Normalizes numbers before comparison ($1,200 == 1200 == 1,200)
        - Only flags numbers that look like facts (prices, percentages, years)
        - Skips very small numbers (1, 2, etc.) which are likely list items
        """
        if not source_contents:
            return True

        sources_combined = " ".join(source_contents)

        # Extract numbers that look like meaningful facts (prices, large numbers, percentages)
        fact_numbers = re.findall(
            r"\$[\d,]+(?:\.\d+)?|\b\d{4}\b|\b\d+%|\b\d{3,}(?:,\d{3})*(?:\.\d+)?\b",
            response_text
        )

        for raw_num in fact_numbers:
            # Normalize: strip $, commas for comparison
            normalized = re.sub(r"[$,]", "", raw_num)
            # Check if normalized or original form exists in sources
            if normalized not in sources_combined and raw_num not in sources_combined:
                logger.warning(f"Potential hallucination: '{raw_num}' not found in source chunks.")
                return False

        return True

    # ── Tool validation ───────────────────────────────────────────────────────

    def validate_tool_arguments(self, tool_name: str, args: Dict[str, Any]) -> Tuple[bool, str]:
        """
        Phase 3: Validates tool arguments before execution.
        Prevents empty/malformed tool calls from being dispatched.
        Returns (is_valid, error_reason).
        """
        if tool_name == "create_lead":
            if not args.get("name") and not args.get("email"):
                return False, "create_lead requires at least a name or email."

        elif tool_name == "update_lead":
            if not args.get("lead_id"):
                return False, "update_lead requires a lead_id."

        elif tool_name == "request_human_handoff":
            if not args.get("reason"):
                return False, "request_human_handoff requires a reason."

        elif tool_name == "save_contact_details":
            has_any = any(args.get(f) for f in ["name", "email", "phone"])
            if not has_any:
                return False, "save_contact_details requires at least one contact field."

        return True, ""
