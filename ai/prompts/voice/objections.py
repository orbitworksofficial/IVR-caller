from dataclasses import dataclass
from typing import List, Optional, Dict
from ai.tools.schemas import CheckConsentSchema

@dataclass
class Objection:
    category: str
    trigger_phrases: List[str]
    acknowledge_template: str
    counter_template: str
    soft_close_template: str

OBJECTION_LIBRARY: Dict[str, Objection] = {
    "PRICE": Objection(
        category="PRICE",
        trigger_phrases=["too expensive", "expensive", "can't afford", "what does it cost", "out of budget", "cheaper options", "can't justify", "justify the cost", "cost"],
        acknowledge_template="I completely understand — budget is always an important factor.",
        counter_template="Our value and ROI are strong, and we offer flexible payment options like 50% upfront and 50% on delivery. We can also schedule a free discovery call to assess fit.",
        soft_close_template="No pressure at all. Would it be helpful if I sent over some information about our packages by email?"
    ),
    "TIMING": Objection(
        category="TIMING",
        trigger_phrases=["not the right time", "too busy", "maybe later", "not ready yet", "next quarter"],
        acknowledge_template="Completely fair — timing is everything.",
        counter_template="We can start with a low-commitment next step like a 15-minute call or I can email you some info. A short discovery call doesn't commit you to anything.",
        soft_close_template="How about I follow up in a few weeks when things settle down?"
    ),
    "TRUST": Objection(
        category="TRUST",
        trigger_phrases=["never heard of you", "how do I know", "can I see examples", "do you have references", "what results have you got"],
        acknowledge_template="That is a completely fair question — trust is everything.",
        counter_template="Orbit Works has extensive experience delivering results. We offer a free discovery call with no obligation, and I can send you case studies or our portfolio.",
        soft_close_template="Would it help if I sent you some examples of our work first?"
    ),
    "NEED_TO_THINK": Objection(
        category="NEED_TO_THINK",
        trigger_phrases=["let me think", "need to discuss", "have to talk to my partner", "not sure", "need more time"],
        acknowledge_template="Of course — this is an important decision and it deserves proper thought.",
        counter_template="I can send you a summary by email to review at your convenience, or we can schedule a quick follow-up call at a specific time.",
        soft_close_template="Would it help if I sent you a brief summary by email so you have something to share with your team?"
    ),
    "ALREADY_HAVE_PROVIDER": Objection(
        category="ALREADY_HAVE_PROVIDER",
        trigger_phrases=["already have someone", "using another agency", "happy with current provider", "have an in-house team"],
        acknowledge_template="That is great — it sounds like you are already investing in this area.",
        counter_template="Many of our clients appreciate a second opinion or competitive audit. We are AI-native and multi-channel, which often provides a different perspective.",
        soft_close_template="Would a quick 15-minute second-opinion call be valuable, just to benchmark?"
    )
}

def detect_objection(user_text: str) -> Optional[Objection]:
    text_lower = user_text.lower()
    for objection in OBJECTION_LIBRARY.values():
        for phrase in objection.trigger_phrases:
            if phrase.lower() in text_lower:
                return objection
    return None

def get_objection_response(objection: Objection, attempt_number: int) -> str:
    if attempt_number == 1:
        return objection.counter_template
    return objection.soft_close_template

def get_objection_as_knowledge_chunks() -> List[Dict]:
    chunks = []
    for objection in OBJECTION_LIBRARY.values():
        chunks.append({
            "category": "objection_handling",
            "content": f"Objection: {objection.category}. Triggers: {', '.join(objection.trigger_phrases)}. Acknowledge: {objection.acknowledge_template}. Counter: {objection.counter_template}. Soft close: {objection.soft_close_template}."
        })
    return chunks
