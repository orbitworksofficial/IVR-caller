import logging
from enum import Enum
from typing import Any, Dict, List, Optional
from ai.providers.base import LLMProvider

logger = logging.getLogger(__name__)


class RouteReason(str, Enum):
    """Why a particular model was selected."""
    DEFAULT = "default_luna"
    TOOL_CALL = "tool_call_complexity"
    LONG_CONTEXT = "long_context"
    MULTI_PART = "multi_part_query"
    FORCED = "forced_complex"
    FALLBACK = "error_fallback"


LUNA = "gpt-5.6-luna"
TERRA = "gpt-5.6-terra"

# Thresholds
LONG_CONTEXT_CHARS = 3000      # total chars in messages → upgrade to Terra
MULTI_PART_THRESHOLD = 3       # count of "?" in message → multi-part query


class ModelRouter:
    """
    Routes generation queries dynamically between Luna (cost-optimised default)
    and Terra (selective fallback for complexity).

    Cost policy from the plan:
    - FAQ / normal support → Luna
    - Consultative sales → Luna unless complexity signals detected
    - Complex multi-part / tool calls with structured output → Terra
    - Unknown facts → no model upgrade; abstain or hand off
    """

    def __init__(self, provider: LLMProvider):
        self.provider = provider

    def determine_route(
        self,
        messages: List[Dict[str, str]],
        requires_tool_call: bool = False,
        force_complex: bool = False,
    ) -> tuple[str, RouteReason]:
        """
        Returns (model_name, reason) based on routing signals.
        """
        if force_complex:
            return TERRA, RouteReason.FORCED

        if requires_tool_call:
            return TERRA, RouteReason.TOOL_CALL

        # Check last user message for multi-part queries
        last_user_msg = next(
            (m.get("content", "") for m in reversed(messages) if m.get("role") == "user"),
            ""
        )
        if last_user_msg.count("?") >= MULTI_PART_THRESHOLD:
            return TERRA, RouteReason.MULTI_PART

        # Check total context length
        total_chars = sum(len(m.get("content", "")) for m in messages)
        if total_chars > LONG_CONTEXT_CHARS:
            return TERRA, RouteReason.LONG_CONTEXT

        return LUNA, RouteReason.DEFAULT

    async def generate(
        self,
        messages: List[Dict[str, str]],
        requires_tool_call: bool = False,
        force_complex: bool = False,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Executes generate() with automatic model selection and error fallback.
        Injects routing metadata into the result for telemetry logging.
        """
        model, reason = self.determine_route(
            messages,
            requires_tool_call=requires_tool_call,
            force_complex=force_complex,
        )
        logger.info(f"ModelRouter: selected '{model}' | reason='{reason.value}'")

        try:
            result = await self.provider.generate(messages=messages, model=model, **kwargs)
            result["routing"] = {"model": model, "reason": reason.value, "fallback": False}
            return result

        except Exception as e:
            fallback = TERRA if model == LUNA else LUNA
            logger.warning(
                f"ModelRouter: '{model}' failed ({e}). Falling back to '{fallback}'."
            )
            result = await self.provider.generate(
                messages=messages, model=fallback, **kwargs
            )
            result["routing"] = {
                "model": fallback,
                "reason": RouteReason.FALLBACK.value,
                "fallback": True,
                "original_model": model,
                "original_error": str(e),
            }
            return result

    async def tool_call(
        self,
        messages: List[Dict[str, str]],
        tools: List[Dict[str, Any]],
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Tool calls always route to Terra for structured output reliability.
        """
        logger.info(f"ModelRouter: tool_call → routing to '{TERRA}'")
        result = await self.provider.tool_call(
            messages=messages, tools=tools, model=TERRA, **kwargs
        )
        result["routing"] = {"model": TERRA, "reason": RouteReason.TOOL_CALL.value}
        return result
