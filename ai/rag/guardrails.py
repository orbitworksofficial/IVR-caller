import re
from typing import List, Dict, Any

class GroundingGuardrails:
    """Enforces prompt safety boundaries, grounding compliance, and LLM output sanity checks."""

    def __init__(self, confidence_threshold: float = 0.7):
        self.confidence_threshold = confidence_threshold
        # Quick check heuristics for prompt injection keywords
        self.injection_keywords = [
            r"ignore previous instructions",
            r"system prompt",
            r"you are now a",
            r"bypass rules",
            r"reveal your setup"
        ]

    def is_safe_input(self, user_query: str) -> bool:
        """
        Analyzes the user's input query for potential prompt injection attempts.
        """
        for pattern in self.injection_keywords:
            if re.search(pattern, user_query, re.IGNORECASE):
                return False
        return True

    def meets_grounding_threshold(self, retrieval_results: List[Dict[str, Any]]) -> bool:
        """
        Validates whether retrieved knowledge sources have sufficient relevance score.
        If all results fall below the grounding confidence threshold, the bot should abstain.
        """
        if not retrieval_results:
            return False
            
        # Check if the top result exceeds our required threshold
        top_score = retrieval_results[0].get("score", 0.0)
        return top_score >= self.confidence_threshold

    def verify_response_grounded(self, response_text: str, source_contents: List[str]) -> bool:
        """
        Performs a sanity check on generated responses to detect outright hallucinated terms.
        E.g., if response references brand names, dates, or prices not in the source documents.
        """
        # Simple heuristic check: extract numeric representations and match with sources
        numbers_in_response = re.findall(r"\$?\d+(?:\.\d+)?", response_text)
        sources_combined = " ".join(source_contents)

        for num in numbers_in_response:
            # Check if numbers/prices mentioned in response exist in source chunks
            if num not in sources_combined:
                # Potential hallucination alert
                return False
        return True
