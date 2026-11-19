"""
Structured objection library for voice calls (implementation plan §07).

Phase 4: the library is tenant-configurable. The defaults below are
business-neutral (``{business_name}`` is substituted at render time) and a
tenant can override any field of any category — or add a brand-new category —
through ``ivr_bot_configs.voice_script["objections"]`` without code changes.
See ``build_objection_library`` and docs/VOICE_REPLICATION.md.
"""

import re
from dataclasses import dataclass, field, replace
from typing import Any, Dict, List, Optional
from ai.tools.schemas import CheckConsentSchema


@dataclass
class Objection:
    category: str
    trigger_phrases: List[str]
    acknowledge_template: str
    counter_template: str
    soft_close_template: str
    # Phrases that look like a trigger but are NOT an objection
    # (e.g. "what does it cost" is a pricing question, not a price objection).
    exclude_phrases: List[str] = field(default_factory=list)

    def render(self, text: str, business_name: str = "") -> str:
        """Substitute {business_name} safely (unknown braces are left untouched)."""
        return text.replace("{business_name}", business_name or "our team")


OBJECTION_LIBRARY: Dict[str, Objection] = {
    "PRICE": Objection(
        category="PRICE",
        trigger_phrases=[
            "too expensive", "expensive", "can't afford", "cannot afford", "out of budget",
            "over budget", "out of our budget", "cheaper options", "cheaper option",
            "can't justify", "justify the cost", "too much money", "costs too much",
            "too pricey", "pricey",
        ],
        acknowledge_template="I completely understand — budget is always an important factor.",
        counter_template=(
            "The goal is a clear return on the investment, and we can start with a free "
            "discovery call to scope something that fits your budget before you commit to anything."
        ),
        soft_close_template=(
            "No pressure at all. Would it be helpful if I sent over some information "
            "about our packages by email?"
        ),
    ),
    "TIMING": Objection(
        category="TIMING",
        trigger_phrases=[
            "not the right time", "too busy", "maybe later", "not ready yet", "next quarter",
            "bad time", "not right now", "next year", "call me later", "in a few months",
        ],
        acknowledge_template="Completely fair — timing is everything.",
        counter_template=(
            "We can start with a low-commitment next step like a 15-minute call, or I can "
            "email you some information. A short discovery call doesn't commit you to anything."
        ),
        soft_close_template="How about I follow up in a few weeks when things settle down?",
    ),
    "TRUST": Objection(
        category="TRUST",
        trigger_phrases=[
            "never heard of you", "how do i know", "can i see examples", "do you have references",
            "what results have you got", "is this a scam", "sounds like a scam", "why should i trust",
        ],
        acknowledge_template="That is a completely fair question — trust is everything.",
        counter_template=(
            "{business_name} is happy to share examples of past work, and the first discovery "
            "call is free with no obligation, so you can judge the fit for yourself."
        ),
        soft_close_template="Would it help if I sent you some examples of our work first?",
    ),
    "NEED_TO_THINK": Objection(
        category="NEED_TO_THINK",
        trigger_phrases=[
            "let me think", "need to think", "need to discuss", "have to talk to my partner",
            "talk to my partner", "check with my boss", "talk to my team", "not sure yet",
            "not sure about this", "need more time", "sleep on it",
        ],
        acknowledge_template="Of course — this is an important decision and it deserves proper thought.",
        counter_template=(
            "I can send you a summary by email to review at your convenience, or we can "
            "schedule a quick follow-up call at a specific time."
        ),
        soft_close_template=(
            "Would it help if I sent you a brief summary by email so you have something "
            "to share with your team?"
        ),
    ),
    "ALREADY_HAVE_PROVIDER": Objection(
        category="ALREADY_HAVE_PROVIDER",
        trigger_phrases=[
            "already have someone", "using another agency", "happy with current provider",
            "have an in-house team", "already work with", "already have an agency",
            "happy with our current", "we have a vendor",
        ],
        acknowledge_template="That is great — it sounds like you are already investing in this area.",
        counter_template=(
            "Many businesses find a second opinion useful, even when they're happy with "
            "their current team — it's a quick way to benchmark what you're getting."
        ),
        soft_close_template="Would a quick 15-minute second-opinion call be valuable, just to benchmark?",
    ),
}

_OBJECTION_FIELDS = {
    "trigger_phrases", "acknowledge_template", "counter_template",
    "soft_close_template", "exclude_phrases",
}


def build_objection_library(overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Objection]:
    """
    Merge tenant overrides over the default library (Phase 4 config pattern —
    the same "DB values win over defaults" rule as TenantConfigResolver).

    overrides format (from ivr_bot_configs.voice_script["objections"]):
        {
          "PRICE": {"counter_template": "...", "extra_trigger_phrases": ["..."]},
          "COMPETITOR_CHEAPER": {          # brand-new category
              "trigger_phrases": ["..."], "acknowledge_template": "...",
              "counter_template": "...", "soft_close_template": "..."
          },
          "TRUST": {"disabled": true}       # drop a category for this tenant
        }
    """
    library = {k: replace(v) for k, v in OBJECTION_LIBRARY.items()}
    if not overrides or not isinstance(overrides, dict):
        return library

    for raw_key, spec in overrides.items():
        if not isinstance(spec, dict):
            continue
        key = str(raw_key).upper()
        if spec.get("disabled"):
            library.pop(key, None)
            continue

        updates = {k: v for k, v in spec.items() if k in _OBJECTION_FIELDS and v}
        extra = [p for p in (spec.get("extra_trigger_phrases") or []) if isinstance(p, str)]

        if key in library:
            obj = replace(library[key], **updates)
            if extra:
                obj.trigger_phrases = list(obj.trigger_phrases) + extra
            library[key] = obj
            continue

        # New category: must be complete to be usable.
        required = ("trigger_phrases", "acknowledge_template", "counter_template", "soft_close_template")
        if all(updates.get(r) for r in required):
            library[key] = Objection(
                category=key,
                trigger_phrases=list(updates["trigger_phrases"]) + extra,
                acknowledge_template=updates["acknowledge_template"],
                counter_template=updates["counter_template"],
                soft_close_template=updates["soft_close_template"],
                exclude_phrases=list(updates.get("exclude_phrases") or []),
            )
    return library


def _matches(phrase: str, text: str) -> bool:
    # Word-boundary match so "pricey" doesn't fire on unrelated substrings.
    return re.search(r"(?<!\w)" + re.escape(phrase.lower()) + r"(?!\w)", text) is not None


def detect_objection(user_text: str, library: Optional[Dict[str, Objection]] = None) -> Optional[Objection]:
    text_lower = (user_text or "").lower().replace("’", "'")
    for objection in (library or OBJECTION_LIBRARY).values():
        if any(_matches(p, text_lower) for p in objection.exclude_phrases):
            continue
        for phrase in objection.trigger_phrases:
            if _matches(phrase, text_lower):
                return objection
    return None


def get_objection_response(objection: Objection, attempt_number: int) -> str:
    if attempt_number == 1:
        return objection.counter_template
    return objection.soft_close_template


def build_objection_instruction(objection: Objection, attempt_number: int, business_name: str = "") -> str:
    """
    The per-turn instruction injected into the voice prompt: acknowledge in one
    sentence, then either counter (attempt 1) or soft-close (attempt 2+).
    """
    ack = objection.render(objection.acknowledge_template, business_name)
    if attempt_number <= 1:
        body = objection.render(objection.counter_template, business_name)
        return (
            f"The caller raised a {objection.category} objection (first time). "
            f"Acknowledge it in one sentence, e.g. \"{ack}\", then re-address it once using "
            f"approved knowledge only, along the lines of: \"{body}\""
        )
    body = objection.render(objection.soft_close_template, business_name)
    return (
        f"The caller raised the {objection.category} objection again. Do NOT push a third time. "
        f"Acknowledge briefly, e.g. \"{ack}\", then soft close: \"{body}\""
    )


def get_objection_as_knowledge_chunks(library: Optional[Dict[str, Objection]] = None) -> List[Dict]:
    chunks = []
    for objection in (library or OBJECTION_LIBRARY).values():
        chunks.append({
            "category": "objection_handling",
            "content": (
                f"Objection: {objection.category}. Triggers: {', '.join(objection.trigger_phrases)}. "
                f"Acknowledge: {objection.acknowledge_template}. Counter: {objection.counter_template}. "
                f"Soft close: {objection.soft_close_template}."
            ),
        })
    return chunks
