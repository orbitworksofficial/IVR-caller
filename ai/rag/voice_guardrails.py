"""
Live-call grounding, abstention and disclosure hardening — Phase 3 (Hammad).

Chat can show a correction after the fact; a phone call cannot un-say a wrong
price. So voice gets deterministic guards *around* the LLM, not just prompt
instructions:

  VoiceGuardrails.classify_turn()  — before the LLM: injection, opt-out,
                                     AI-disclosure question, exempt intents
  VoiceGuardrails.should_hard_abstain() — empty retrieval on a factual
                                     question → speak the tenant's abstain
                                     phrase, never improvise
  SpokenOutputFilter               — between the LLM and TTS, sentence by
                                     sentence: strips markdown/URLs/emojis,
                                     blocks numbers (prices, %, years) that
                                     are not in the approved sources, and
                                     guarantees an honest AI disclosure when
                                     the caller asked.

Everything here is pure Python (no I/O) so it adds no latency and is fully
unit-tested in tests/test_voice_hardening.py.
"""

import logging
import re
from dataclasses import dataclass, field
from typing import AsyncGenerator, AsyncIterator, Iterable, List, Optional

from ai.rag.guardrails import GroundingGuardrails

logger = logging.getLogger(__name__)

# Kept in sync with apps/voice_gateway/opt_out.py — the gateway writes the DNC
# entry; this copy lets the AI layer *say* the right thing without importing
# the gateway package.
_OPT_OUT_PATTERNS = [
    r"\bremove (me|my (phone )?number)\b",
    r"\btake (me|my (phone )?number) off\b",
    r"\b(do not|don't|dont|never) (call|ring|phone) (me|this number|us)\b",
    r"\bstop (calling|ringing|phoning)\b",
    r"\b(put|add) (me|my (phone )?number) (on|to) (your |the )?(do not|don't) call\b",
    r"\bunsubscribe\b",
    r"\bno more (calls|phone calls)\b",
]
_OPT_OUT = re.compile("|".join(_OPT_OUT_PATTERNS), re.IGNORECASE)

_DISCLOSURE_Q = re.compile(
    r"\b("
    r"are you (a |an )?(real|human|person|people|robot|bot|ai|a\.i\.|machine|computer|recording|automated|live person|actual person)"
    r"|am i (talking|speaking) (to|with) (a |an )?(real|human|person|robot|bot|machine|computer|ai|a\.i\.|recording|live person|actual person)"
    r"|is this (a |an )?(robot|bot|real person|human|recording|ai|a\.i\.|automated|machine|computer|live person)"
    r"|is (this|that) (a )?(recorded|automated)"
    r"|you('re| are) (a |an )?(robot|bot|ai|machine)"
    r")\b",
    re.IGNORECASE,
)
_AI_ADMISSION = re.compile(
    r"\b(ai|a\.i\.|artificial intelligence|virtual assistant|automated assistant|digital assistant)\b",
    re.IGNORECASE,
)
_QUESTION_START = re.compile(
    r"^(so |and |but |um |uh |okay |ok |hey |hi |also )*"
    r"(what|what's|whats|when|where|which|who|whom|whose|why|how|is|are|do|does|did|can|could|"
    r"will|would|should|have|has|may|tell me|explain)\b",
    re.IGNORECASE,
)
_FOLLOWUP = re.compile(
    r"\b(repeat that|say that again|come again|pardon|what did you say|didn't catch|did not catch|"
    r"can you hear me|are you there|who is this|who am i (speaking|talking) (to|with)|what was that)\b",
    re.IGNORECASE,
)

# ── Spoken-output sanitising ─────────────────────────────────────────────────
_URL = re.compile(
    r"(https?://\S+|www\.\S+|\b[\w-]+(\.[\w-]+)*\.(com|net|org|io|ai|co|app|dev|biz|info|us|uk)(/\S*)?)",
    re.IGNORECASE,
)
_EMAIL = re.compile(r"\b([\w.+-]+)@([\w-]+(?:\.[\w-]+)+)\b")
_MD_EMPH = re.compile(r"(\*\*|__|\*|`+|~~)")
_MD_HEADING = re.compile(r"(^|\n)\s*#{1,6}\s*")
_MD_BULLET = re.compile(r"(^|\n)\s*([-*•]|\d+[.)])\s+")
_MD_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_EMOJI = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F2FF\U0000FE0F\U0000200D]+"
)
# Same fact-number definition as GroundingGuardrails.verify_response_grounded.
_FACT_NUMBER = re.compile(r"\$[\d,]+(?:\.\d+)?[kKmM]?|\b\d{4}\b|\b\d+(?:\.\d+)?\s?%|\b\d{3,}(?:,\d{3})*(?:\.\d+)?\b")
# Sentence end = terminal punctuation followed by whitespace (so "orb-itworks.com"
# and "$1.5" are never split mid-token).
_SENTENCE_END = re.compile(r"[.!?…]+[\"')\]]*\s+")

URL_REPLACEMENT = "a link we can text or email to you"


def _email_to_speech(match: re.Match) -> str:
    user, domain = match.group(1), match.group(2)
    return f"{user.replace('.', ' dot ')} at {domain.replace('.', ' dot ')}"


def sanitize_for_speech(text: str) -> str:
    """Make LLM text safe and natural for TTS: no markdown, URLs or emojis."""
    if not text:
        return ""
    out = _MD_LINK.sub(r"\1", text)
    out = _EMAIL.sub(_email_to_speech, out)
    out = _URL.sub(URL_REPLACEMENT, out)
    out = _MD_HEADING.sub(r"\1", out)
    out = _MD_BULLET.sub(r"\1", out)
    out = _MD_EMPH.sub("", out)
    out = out.replace("#", "")
    out = _EMOJI.sub("", out)
    out = re.sub(r"\s+", " ", out).strip()
    # Avoid "a link we can text or email to you a link we can text..." repeats.
    out = re.sub(rf"({re.escape(URL_REPLACEMENT)})(\W+\1)+", r"\1", out)
    return out


def _normalise_number(raw: str) -> str:
    return re.sub(r"[$,\s%]", "", raw).rstrip("kKmM")


def ungrounded_numbers(sentence: str, sources: str) -> List[str]:
    """Fact-like numbers in `sentence` that do not appear anywhere in `sources`."""
    if not sentence:
        return []
    src = sources or ""
    src_digits = re.sub(r"[,\s]", "", src)
    missing = []
    for raw in _FACT_NUMBER.findall(sentence):
        norm = _normalise_number(raw)
        if not norm:
            continue
        if raw in src or norm in src or norm in src_digits:
            continue
        missing.append(raw.strip())
    return missing


@dataclass
class TurnClassification:
    injection: bool = False
    opt_out: bool = False
    asks_if_ai: bool = False
    is_question: bool = False
    exempt_from_abstain: bool = False
    reasons: List[str] = field(default_factory=list)


class VoiceGuardrails(GroundingGuardrails):
    """GroundingGuardrails (shared with chat) plus live-call specific checks."""

    def is_opt_out(self, text: str) -> bool:
        return bool(text and _OPT_OUT.search(text.replace("’", "'")))

    def asks_if_ai(self, text: str) -> bool:
        return bool(text and _DISCLOSURE_Q.search(text.replace("’", "'")))

    def is_question(self, text: str) -> bool:
        t = (text or "").strip()
        return t.endswith("?") or bool(_QUESTION_START.match(t))

    def is_conversational_followup(self, text: str) -> bool:
        return bool(text and _FOLLOWUP.search(text))

    def classify_turn(self, transcript: str, is_objection: bool = False) -> TurnClassification:
        c = TurnClassification()
        t = transcript or ""
        if not self.is_safe_input(t):
            c.injection = True
            c.reasons.append("injection")
        if self.is_opt_out(t):
            c.opt_out = True
            c.reasons.append("opt_out")
        if self.asks_if_ai(t):
            c.asks_if_ai = True
            c.reasons.append("ai_disclosure_question")
        c.is_question = self.is_question(t)

        exempt = (
            c.asks_if_ai or c.opt_out or is_objection
            or self.is_chitchat(t) or self.is_contact_info(t)
            or self.is_booking_request(t) or self.is_handoff_request(t)
            or self.is_conversational_followup(t)
        )
        c.exempt_from_abstain = exempt
        return c

    def should_hard_abstain(
        self,
        classification: TurnClassification,
        retrieved_chunks: List[str],
        tool_used: bool = False,
        retrieval_failed: bool = False,
    ) -> bool:
        """
        Deterministic abstention for live calls: a factual question with no
        approved knowledge behind it (or a failed retrieval) gets the tenant's
        abstain phrase instead of an LLM answer. Conversational turns,
        bookings, handoffs, objections and tool-answered turns are exempt.
        """
        if classification.exempt_from_abstain or tool_used:
            return False
        if not classification.is_question:
            return False
        return retrieval_failed or not retrieved_chunks


class SpokenOutputFilter:
    """
    Sits between the LLM token stream and TTS. Re-chunks tokens into whole
    sentences, sanitises each one for speech and enforces grounding and
    disclosure before a single word reaches the caller.

    Usage:
        f = SpokenOutputFilter(sources=..., abstain_phrase=..., require_ai_disclosure=...)
        async for sentence in f.filter(token_stream):
            ...  # feed to TTS
        f.spoken_text, f.violations
    """

    def __init__(
        self,
        sources: str = "",
        abstain_phrase: str = "",
        require_ai_disclosure: bool = False,
        disclosure_sentence: str = "I'm an AI assistant.",
        strict_numeric_grounding: bool = True,
    ):
        self.sources = sources or ""
        self.abstain_phrase = abstain_phrase
        self.require_ai_disclosure = require_ai_disclosure
        self.disclosure_sentence = disclosure_sentence
        self.strict_numeric_grounding = strict_numeric_grounding
        self.violations: List[str] = []
        self._spoken: List[str] = []
        self._first_sentence_done = False
        self.stopped = False

    @property
    def spoken_text(self) -> str:
        return " ".join(self._spoken).strip()

    def _process_sentence(self, raw: str) -> List[str]:
        sentence = sanitize_for_speech(raw)
        if not sentence:
            return []
        out: List[str] = []

        if not self._first_sentence_done:
            self._first_sentence_done = True
            if self.require_ai_disclosure and not _AI_ADMISSION.search(sentence):
                out.append(self.disclosure_sentence)
                self.violations.append("missing_ai_disclosure_fixed")

        if self.strict_numeric_grounding:
            missing = ungrounded_numbers(sentence, self.sources)
            if missing:
                logger.warning(f"Blocked ungrounded spoken numbers {missing}: {sentence!r}")
                self.violations.append(f"ungrounded_numbers:{','.join(missing)}")
                if self.abstain_phrase:
                    out.append(self.abstain_phrase)
                self.stopped = True
                return out

        out.append(sentence)
        return out

    def _emit(self, sentences: Iterable[str]) -> List[str]:
        emitted = []
        for s in sentences:
            self._spoken.append(s)
            emitted.append(s)
        return emitted

    async def filter(self, tokens: AsyncIterator[str]) -> AsyncGenerator[str, None]:
        buffer = ""
        async for token in tokens:
            if self.stopped:
                continue  # drain the LLM stream without speaking it
            buffer += token or ""
            while True:
                m = _SENTENCE_END.search(buffer)
                if not m:
                    break
                raw, buffer = buffer[: m.end()], buffer[m.end():]
                for s in self._emit(self._process_sentence(raw)):
                    yield s
                if self.stopped:
                    buffer = ""
                    break
        if buffer.strip() and not self.stopped:
            for s in self._emit(self._process_sentence(buffer)):
                yield s
        if not self._spoken and self.require_ai_disclosure:
            for s in self._emit([self.disclosure_sentence]):
                yield s
