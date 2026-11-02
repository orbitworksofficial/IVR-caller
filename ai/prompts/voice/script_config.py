"""
Tenant-specific call scripts — Phase 4 (implementation plan §13, Hammad).

Reuses the chatbot's TenantConfigResolver pattern: safe defaults in code,
per-tenant values in the database, DB values win. Everything a new client
needs to change about how the agent *talks* on the phone lives in one JSONB
column, ``ivr_bot_configs.voice_script``, so onboarding a tenant onto voice is
a configuration exercise, not a code change.

Supported keys (all optional) — validated by ``validate_voice_script``:

    greetings          {"inbound_support": str, "inbound_sales": str, "outbound": str}
                       ({business_name} is substituted)
    filler_phrases     [str, ...]       dead-air fillers
    abstain_phrase     str              said when knowledge has no answer
    handoff_phrase     str              said before a warm transfer
    opt_out_ack        str              said when the caller asks not to be called
    injection_reply    str              said when a prompt-injection is detected
    blocked_notice     str              said when an outbound call is blocked at start
    default_call_reason str             outbound reason when the campaign has none
    extra_rules        [str, ...] | str business-specific rules added to the prompt
    objections         {...}            see ai/prompts/voice/objections.build_objection_library
    transfer_phone     str (E.164)      warm-transfer destination for this bot
    call_model         str              LLM for this bot's calls (default Luna)
    hard_abstain       bool             deterministic abstention on empty retrieval (default true)
    strict_numeric_grounding bool       block spoken numbers missing from sources (default true)

Legacy columns ``ivr_bot_configs.abstain_phrase`` / ``filler_phrase`` and the
chatbot's ``escalation_phone`` are honoured as fallbacks.
"""

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from ai.prompts.voice.objections import Objection, build_objection_library
from ai.prompts.voice.templates import (
    DEFAULT_ABSTAIN_PHRASE,
    VOICE_FILLER_PHRASES,
    VOICE_GREETING_TEMPLATES,
    VOICE_HANDOFF_PHRASES,
)

logger = logging.getLogger(__name__)

DEFAULT_CALL_MODEL = "gpt-5.6-luna"
ALLOWED_CALL_MODELS = {"gpt-5.6-luna", "gpt-5.6-terra"}

DEFAULT_OPT_OUT_ACK = (
    "Understood. I've noted that you don't want to receive calls from us, "
    "and you won't be called again. Thank you for your time, goodbye."
)
DEFAULT_INJECTION_REPLY = "I'm here to help with {business_name} questions. What can I help you with today?"
DEFAULT_BLOCKED_NOTICE = "I'm sorry, we cannot connect this call at this time. Goodbye."

_E164 = re.compile(r"^\+[1-9]\d{6,14}$")
_STRING_KEYS = (
    "abstain_phrase", "handoff_phrase", "opt_out_ack", "injection_reply",
    "blocked_notice", "default_call_reason", "transfer_phone", "call_model",
)
_KNOWN_KEYS = set(_STRING_KEYS) | {
    "greetings", "filler_phrases", "extra_rules", "objections",
    "hard_abstain", "strict_numeric_grounding",
}


@dataclass
class VoiceScript:
    business_name: str = "our company"
    greetings: Dict[str, str] = field(default_factory=lambda: dict(VOICE_GREETING_TEMPLATES))
    filler_phrases: List[str] = field(default_factory=lambda: list(VOICE_FILLER_PHRASES))
    abstain_phrase: str = DEFAULT_ABSTAIN_PHRASE
    handoff_phrase: str = VOICE_HANDOFF_PHRASES[0]
    opt_out_ack: str = DEFAULT_OPT_OUT_ACK
    injection_reply: str = DEFAULT_INJECTION_REPLY
    blocked_notice: str = DEFAULT_BLOCKED_NOTICE
    default_call_reason: str = ""
    extra_rules: List[str] = field(default_factory=list)
    objections: Dict[str, Objection] = field(default_factory=build_objection_library)
    transfer_phone: str = ""
    call_model: str = DEFAULT_CALL_MODEL
    hard_abstain: bool = True
    strict_numeric_grounding: bool = True

    def render(self, text: str) -> str:
        return (text or "").replace("{business_name}", self.business_name)

    @property
    def extra_rules_text(self) -> str:
        return "\n".join(f"- {r}" for r in self.extra_rules if r and r.strip())


def validate_voice_script(script: Any) -> Tuple[bool, List[str]]:
    """Returns (ok, problems). Unknown keys are reported but not fatal."""
    problems: List[str] = []
    if script in (None, {}):
        return True, problems
    if not isinstance(script, dict):
        return False, ["voice_script must be a JSON object"]

    fatal = False
    for key in script:
        if key not in _KNOWN_KEYS:
            problems.append(f"unknown key '{key}' (ignored)")

    for key in _STRING_KEYS:
        if key in script and not isinstance(script[key], str):
            problems.append(f"'{key}' must be a string")
            fatal = True

    if "greetings" in script:
        g = script["greetings"]
        if not isinstance(g, dict) or not all(isinstance(v, str) for v in g.values()):
            problems.append("'greetings' must map greeting keys to strings")
            fatal = True
        else:
            for k in g:
                if k not in VOICE_GREETING_TEMPLATES:
                    problems.append(f"unknown greeting '{k}' (use one of {sorted(VOICE_GREETING_TEMPLATES)})")

    if "filler_phrases" in script:
        f = script["filler_phrases"]
        if not isinstance(f, list) or not f or not all(isinstance(x, str) and x.strip() for x in f):
            problems.append("'filler_phrases' must be a non-empty list of strings")
            fatal = True

    if "extra_rules" in script and not isinstance(script["extra_rules"], (str, list)):
        problems.append("'extra_rules' must be a string or a list of strings")
        fatal = True

    if "objections" in script and not isinstance(script["objections"], dict):
        problems.append("'objections' must be an object keyed by category")
        fatal = True

    for key in ("hard_abstain", "strict_numeric_grounding"):
        if key in script and not isinstance(script[key], bool):
            problems.append(f"'{key}' must be true or false")
            fatal = True

    phone = script.get("transfer_phone")
    if isinstance(phone, str) and phone and not _E164.match(phone):
        problems.append("'transfer_phone' must be E.164, e.g. +14435550100")
        fatal = True

    model = script.get("call_model")
    if isinstance(model, str) and model and model not in ALLOWED_CALL_MODELS:
        problems.append(f"'call_model' must be one of {sorted(ALLOWED_CALL_MODELS)}")
        fatal = True

    return not fatal, problems


def resolve_voice_script(
    ivr_config: Optional[Dict[str, Any]] = None,
    bot_config: Optional[Dict[str, Any]] = None,
) -> VoiceScript:
    """
    Builds the effective VoiceScript for one bot: defaults ← legacy columns ←
    voice_script JSONB. An invalid voice_script is ignored as a whole (logged)
    so a bad config can never take a live phone line down.
    """
    ivr = ivr_config or {}
    bot = bot_config or {}
    vs = VoiceScript(business_name=bot.get("name") or "our company")

    # Legacy / shared columns
    if ivr.get("abstain_phrase"):
        vs.abstain_phrase = ivr["abstain_phrase"]
    if ivr.get("filler_phrase"):
        vs.filler_phrases = [ivr["filler_phrase"]]
    escalation = bot.get("escalation_phone") or (bot.get("handoff_settings") or {}).get("phone")
    if isinstance(escalation, str) and _E164.match(escalation):
        vs.transfer_phone = escalation

    raw = ivr.get("voice_script") or {}
    ok, problems = validate_voice_script(raw)
    for p in problems:
        logger.warning(f"voice_script for bot {bot.get('id')}: {p}")
    if not ok:
        logger.error(f"voice_script for bot {bot.get('id')} is invalid — using defaults")
        return vs

    if raw.get("greetings"):
        vs.greetings = {**vs.greetings, **raw["greetings"]}
    if raw.get("filler_phrases"):
        vs.filler_phrases = list(raw["filler_phrases"])
    for key in _STRING_KEYS:
        if raw.get(key):
            setattr(vs, key, raw[key])
    rules = raw.get("extra_rules")
    if isinstance(rules, str) and rules.strip():
        vs.extra_rules = [rules.strip()]
    elif isinstance(rules, list):
        vs.extra_rules = [r for r in rules if isinstance(r, str) and r.strip()]
    if raw.get("objections"):
        vs.objections = build_objection_library(raw["objections"])
    for key in ("hard_abstain", "strict_numeric_grounding"):
        if key in raw:
            setattr(vs, key, raw[key])
    return vs
