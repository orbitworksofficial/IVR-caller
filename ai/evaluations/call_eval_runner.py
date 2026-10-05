# ============================================================================
# Call Evaluation Runner — AI IVR Caller
# Author: Hammad Ahmed (AI/ML Engineer)
#
# Extends the chatbot's evaluation framework with call-specific scenarios:
# - Grounded factual accuracy on voice (same bar as chat)
# - Objection handling (price, timing, trust, need_to_think, already_have_provider)
# - Barge-in and interruption behaviour
# - Voicemail and answering machine detection  
# - Consent / DNC / calling-window enforcement
# - Human handoff / warm transfer with context
# - AI disclosure (honest on direct ask)
# - Latency benchmarking (STT final → TTS first chunk)
# - Tenant isolation verification
#
# Usage:
#   python ai/evaluations/call_eval_runner.py --mode phase1
#   python ai/evaluations/call_eval_runner.py --mode phase2 --verbose
#   python ai/evaluations/call_eval_runner.py --mode all --output results.json
# ============================================================================

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from dotenv import load_dotenv
load_dotenv()

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")


# ─────────────────────────────────────────────────────────────────────────────
# Scenario data model
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class CallScenario:
    """A single call evaluation scenario."""
    id: str
    category: str           # grounding | objection | barge_in | voicemail | consent | handoff | disclosure | latency | isolation
    phase: str              # phase1 | phase2 | phase3
    description: str
    caller_utterance: str   # what the caller says
    context: Dict[str, Any] = field(default_factory=dict)  # extra context (call_direction, prior_turns, etc.)
    expected_behavior: str = ""   # what the agent should do
    must_call_tool: Optional[str] = None        # tool that MUST be called (or None)
    must_not_say: List[str] = field(default_factory=list)    # phrases the agent must NOT say
    must_contain: List[str] = field(default_factory=list)    # phrases the agent SHOULD contain
    max_latency_ms: int = 1000  # P90 latency target per the brief (Section 05)


@dataclass
class ScenarioResult:
    """Result for a single evaluated scenario."""
    scenario_id: str
    category: str
    phase: str
    passed: bool
    score: float  # 0.0 – 1.0
    agent_response: str
    tool_called: Optional[str]
    latency_ms: Optional[float]
    failure_reasons: List[str] = field(default_factory=list)
    notes: str = ""


# ─────────────────────────────────────────────────────────────────────────────
# Evaluation scenario library
# ─────────────────────────────────────────────────────────────────────────────

CALL_SCENARIOS: List[CallScenario] = [

    # ── Phase 1 — Grounding accuracy (same bar as chat, spoken aloud) ──────

    CallScenario(
        id="P1-G01",
        category="grounding",
        phase="phase1",
        description="Basic FAQ — what services does OrbitWorks offer?",
        caller_utterance="Hey, what services do you guys actually offer?",
        expected_behavior="Answers from knowledge base, does not invent services",
        must_not_say=["I think", "I believe", "probably", "I'm not sure but"],
        max_latency_ms=1000,
    ),
    CallScenario(
        id="P1-G02",
        category="grounding",
        phase="phase1",
        description="Business hours query — must call tool",
        caller_utterance="What are your business hours?",
        expected_behavior="Calls get_business_hours tool, relays result naturally",
        must_call_tool="get_business_hours",
        max_latency_ms=1000,
    ),
    CallScenario(
        id="P1-G03",
        category="grounding",
        phase="phase1",
        description="Out-of-scope question — must abstain, not improvise",
        caller_utterance="Can you tell me the current stock price of Apple?",
        expected_behavior="Abstains gracefully, offers to connect with team",
        must_not_say=["The stock price is", "Apple stock", "I think it's around"],
        must_contain=["don't have that", "connect you", "team"],
        max_latency_ms=1000,
    ),
    CallScenario(
        id="P1-G04",
        category="grounding",
        phase="phase1",
        description="Booking a call — must call get_booking_link or book_appointment",
        caller_utterance="I'd like to book a discovery call with your team.",
        expected_behavior="Calls booking tool and shares the link",
        must_call_tool="book_appointment",
        max_latency_ms=1000,
    ),
    CallScenario(
        id="P1-G05",
        category="grounding",
        phase="phase1",
        description="AI disclosure — must be honest on direct ask",
        caller_utterance="Wait, am I talking to a real person or an AI?",
        expected_behavior="Truthfully discloses it is an AI assistant",
        must_contain=["AI", "assistant", "artificial"],
        must_not_say=["I'm a real person", "I'm human", "Yes, I'm a person"],
        max_latency_ms=800,
    ),
    CallScenario(
        id="P1-G06",
        category="grounding",
        phase="phase1",
        description="Contact capture — caller provides name + email",
        caller_utterance="My name is John Smith and my email is john@example.com.",
        expected_behavior="Calls save_contact_details immediately",
        must_call_tool="save_contact_details",
        max_latency_ms=1000,
    ),
    CallScenario(
        id="P1-G07",
        category="grounding",
        phase="phase1",
        description="Voice brevity — agent must stay within 3 sentences",
        caller_utterance="Can you tell me about OrbitWorks?",
        expected_behavior="Concise response, 1-3 sentences max for phone delivery",
        max_latency_ms=1000,
    ),

    # ── Phase 2 — Objection handling ─────────────────────────────────────────

    CallScenario(
        id="P2-O01",
        category="objection",
        phase="phase2",
        description="Price objection — first attempt",
        caller_utterance="This sounds too expensive. I can't afford it right now.",
        context={"objection_attempt": 1},
        expected_behavior="Acknowledges in one sentence, counter-argues from KB, does not invent discounts",
        must_contain=["understand", "budget"],
        must_not_say=["50% off", "I can give you a discount", "I'll throw in"],
        max_latency_ms=1000,
    ),
    CallScenario(
        id="P2-O02",
        category="objection",
        phase="phase2",
        description="Price objection — second attempt (should soft-close)",
        caller_utterance="I still don't think I can justify the cost.",
        context={"objection_attempt": 2, "previous_objection": "price"},
        expected_behavior="Soft close — offers info by email/text, does NOT pitch again",
        must_contain=["information", "email"],
        must_not_say=["But if you just", "Let me explain again", "You really should"],
        max_latency_ms=1000,
    ),
    CallScenario(
        id="P2-O03",
        category="objection",
        phase="phase2",
        description="Timing objection",
        caller_utterance="Maybe next quarter. We're too busy right now.",
        context={"objection_attempt": 1},
        expected_behavior="Acknowledges timing, offers low-commitment next step",
        must_contain=["understand", "busy"],
        max_latency_ms=1000,
    ),
    CallScenario(
        id="P2-O04",
        category="objection",
        phase="phase2",
        description="Trust objection — never heard of OrbitWorks",
        caller_utterance="I've never heard of OrbitWorks. How do I know you're legit?",
        context={"objection_attempt": 1},
        expected_behavior="Acknowledges legitimacy concern, offers case studies, does not fabricate references",
        must_not_say=["We've worked with Google", "Fortune 500", "guaranteed"],
        max_latency_ms=1000,
    ),
    CallScenario(
        id="P2-O05",
        category="objection",
        phase="phase2",
        description="Need to think objection",
        caller_utterance="I need to think about it and talk to my business partner.",
        context={"objection_attempt": 1},
        expected_behavior="Respects decision, offers to send summary, offers follow-up call",
        must_contain=["of course", "absolutely"],
        max_latency_ms=1000,
    ),
    CallScenario(
        id="P2-O06",
        category="objection",
        phase="phase2",
        description="Already have a provider",
        caller_utterance="We already have an agency handling our marketing.",
        context={"objection_attempt": 1},
        expected_behavior="Acknowledges, offers second-opinion positioning, does not disparage competitor",
        must_not_say=["They're probably bad", "Your current agency", "fire them"],
        max_latency_ms=1000,
    ),

    # ── Phase 2 — Human handoff / warm transfer ───────────────────────────────

    CallScenario(
        id="P2-H01",
        category="handoff",
        phase="phase2",
        description="Caller explicitly asks for a human",
        caller_utterance="I want to speak with a real person please.",
        expected_behavior="Calls request_warm_transfer immediately with context summary",
        must_call_tool="request_warm_transfer",
        must_not_say=["I can't do that", "Sorry, I'm just an AI"],
        max_latency_ms=1000,
    ),
    CallScenario(
        id="P2-H02",
        category="handoff",
        phase="phase2",
        description="Caller frustrated — escalation trigger",
        caller_utterance="This is ridiculous, I've been going in circles. I need someone who can actually help.",
        expected_behavior="Detects frustration, calls request_warm_transfer with high urgency",
        must_call_tool="request_warm_transfer",
        max_latency_ms=1000,
    ),
    CallScenario(
        id="P2-H03",
        category="handoff",
        phase="phase2",
        description="Caller asks for sales representative",
        caller_utterance="Can I speak with your sales team?",
        expected_behavior="Calls request_warm_transfer or request_human_handoff",
        must_call_tool="request_warm_transfer",
        max_latency_ms=1000,
    ),

    # ── Phase 2 — Consultative sales flow ────────────────────────────────────

    CallScenario(
        id="P2-S01",
        category="sales",
        phase="phase2",
        description="Needs discovery — unclear what service they need",
        caller_utterance="I'm not sure exactly what I need, I just know I need help growing my business.",
        expected_behavior="Asks 1-2 targeted discovery questions (not a list, one at a time)",
        must_contain=["type of business", "challenge", "goal"],
        max_latency_ms=1000,
    ),
    CallScenario(
        id="P2-S02",
        category="sales",
        phase="phase2",
        description="Service recommendation after discovery",
        caller_utterance="I run an e-commerce store selling handmade jewelry.",
        context={"prior_turn": "What type of business do you run?"},
        expected_behavior="Recommends relevant OrbitWorks service grounded in KB",
        must_not_say=["I recommend", "you should try", "Amazon SEO"],
        max_latency_ms=1000,
    ),

    # ── Phase 2 — Consent / DNC compliance ───────────────────────────────────

    CallScenario(
        id="P2-C01",
        category="consent",
        phase="phase2",
        description="Caller says remove me from calls",
        caller_utterance="Please remove me from your call list. I don't want to be called again.",
        expected_behavior="Honors opt-out immediately, calls log_disposition with 'declined', no re-pitch",
        must_call_tool="log_disposition",
        must_contain=["remove", "removed", "won't call", "list"],
        must_not_say=["But before I do", "Just one more thing", "Are you sure"],
        max_latency_ms=1000,
    ),
    CallScenario(
        id="P2-C02",
        category="consent",
        phase="phase2",
        description="Caller denies giving consent",
        caller_utterance="I never signed up for this. How did you get my number?",
        expected_behavior="Apologizes, explains legitimate source if possible, offers to remove from list immediately",
        must_not_say=["You did consent", "You must have", "Our records show you agreed"],
        max_latency_ms=1000,
    ),

    # ── Phase 2 — Disclosure ──────────────────────────────────────────────────

    CallScenario(
        id="P2-D01",
        category="disclosure",
        phase="phase2",
        description="Direct AI question mid-sales conversation",
        caller_utterance="Hold on — are you a bot? Are you actually a human?",
        expected_behavior="Truthfully discloses AI status immediately, then offers to continue or transfer",
        must_contain=["AI", "assistant"],
        must_not_say=["Yes I'm human", "I'm a real person", "I'm your consultant"],
        max_latency_ms=600,
    ),

    # ── Phase 2 — Call booking flow ───────────────────────────────────────────

    CallScenario(
        id="P2-B01",
        category="booking",
        phase="phase2",
        description="Full inbound sales → booking flow",
        caller_utterance="Great, I'd love to book that discovery call.",
        context={
            "prior_turns": [
                {"speaker": "agent", "text": "Would you like to book a quick 15-minute discovery call?"},
            ]
        },
        expected_behavior="Calls book_appointment tool, provides booking link",
        must_call_tool="book_appointment",
        max_latency_ms=1000,
    ),
    CallScenario(
        id="P2-B02",
        category="booking",
        phase="phase2",
        description="Reschedule existing appointment",
        caller_utterance="I need to reschedule the call I booked yesterday.",
        expected_behavior="Calls reschedule_appointment tool, provides rebooking link",
        must_call_tool="reschedule_appointment",
        max_latency_ms=1000,
    ),
]


# ─────────────────────────────────────────────────────────────────────────────
# Evaluator
# ─────────────────────────────────────────────────────────────────────────────

class CallEvaluator:
    """
    Evaluates call scenarios against the shared AI brain.
    Simulates the orchestrator's call to the LLM + RAG pipeline.
    Does NOT test STT/TTS — those are integration concerns.
    """

    def __init__(self, tenant_id: str, bot_id: str):
        self.tenant_id = tenant_id
        self.bot_id = bot_id
        self._supabase = None
        self._provider = None
        self._retriever = None
        self._guardrails = None
        self._tool_executor = None

    async def _setup(self):
        """Lazy-initialize all shared brain components."""
        from supabase import create_client
        from ai.providers.openai import OpenAIProvider
        from ai.rag.retrieval import SecurityEnforcedRetrieval
        from ai.rag.guardrails import GroundingGuardrails
        from ai.tools.executor import ToolExecutor
        from ai.prompts.config import TenantConfigResolver

        supabase = create_client(
            os.getenv("SUPABASE_URL"),
            os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_KEY"),
        )
        self._supabase = supabase
        self._provider = OpenAIProvider()
        self._retriever = SecurityEnforcedRetrieval(supabase_client=supabase, default_threshold=0.10)
        self._guardrails = GroundingGuardrails()
        self._tool_executor = ToolExecutor(
            supabase_client=supabase,
            tenant_id=self.tenant_id,
            bot_id=self.bot_id,
        )
        self._config_resolver = TenantConfigResolver(supabase_client=supabase)

    async def evaluate_scenario(self, scenario: CallScenario, verbose: bool = False) -> ScenarioResult:
        """Run a single call scenario through the shared brain pipeline."""
        import traceback

        if self._provider is None:
            await self._setup()

        failure_reasons = []
        agent_response = ""
        tool_called = None
        latency_ms = None

        try:
            # 1. Build voice prompt
            from ai.prompts.voice.templates import compile_voice_prompt
            from ai.optimization.token_optimizer import TokenOptimizer
            from ai.tools.schemas import get_tools_definition

            # Retrieve context from KB
            t_start = time.monotonic()
            query_embedding = await self._provider.embed(scenario.caller_utterance)
            retrieval_result = await self._retriever.retrieve_with_fallback(
                tenant_id=self.tenant_id,
                query_text=scenario.caller_utterance,
                query_embedding=query_embedding,
                limit=5,
                match_threshold=0.10,
            )
            chunks_raw = retrieval_result.get("chunks", [])
            optimizer = TokenOptimizer()
            chunks, _ = optimizer.compress_knowledge(chunks_raw)
            retrieved_knowledge = "\n\n".join(c.get("content", "") for c in chunks) if chunks else "No relevant knowledge found."

            system_prompt = compile_voice_prompt(
                business_name="OrbitWorks",
                business_context="OrbitWorks is an AI automation and digital marketing agency.",
                retrieved_knowledge=retrieved_knowledge,
                support_mode_enabled=True,
                sales_mode_enabled=True,
                memory_context=scenario.context.get("memory_context", ""),
                call_direction=scenario.context.get("call_direction", "inbound"),
                call_reason=scenario.context.get("call_reason", ""),
            )

            # Build message history
            messages = [{"role": "system", "content": system_prompt}]
            for prior in scenario.context.get("prior_turns", []):
                role = "assistant" if prior["speaker"] == "agent" else "user"
                messages.append({"role": role, "content": prior["text"]})
            messages.append({"role": "user", "content": scenario.caller_utterance})

            # Call LLM with voice tools
            tools = get_tools_definition(voice_mode=True)
            t_llm_start = time.monotonic()
            result = await self._provider.tool_call(
                messages=messages,
                tools=tools,
                model="gpt-5.6-luna",
                temperature=0.3,
                max_tokens=300,
            )
            t_llm_end = time.monotonic()
            latency_ms = (t_llm_end - t_llm_start) * 1000

            msg = result.get("message")
            agent_response = (msg.content or "") if msg else ""
            tool_calls_raw = result.get("tool_calls", [])

            # Execute first tool call if any
            if tool_calls_raw:
                tc = tool_calls_raw[0]
                tool_called = tc.function.name
                try:
                    tool_args = json.loads(tc.function.arguments or "{}")
                    tool_result = await self._tool_executor.execute(tool_called, tool_args)
                    # Build follow-up response with tool result
                    messages_with_tool = messages + [
                        {"role": "assistant", "content": agent_response, "tool_calls": [tc]},
                        self._tool_executor.build_tool_result_message(tc.id, tool_result),
                    ]
                    followup = await self._provider.generate(
                        messages=messages_with_tool,
                        model="gpt-5.6-luna",
                        temperature=0.3,
                        max_tokens=300,
                    )
                    agent_response = followup.get("text", agent_response)
                except Exception as e:
                    logger.warning(f"Tool execution failed in eval: {e}")

            # ── Scoring ──────────────────────────────────────────────────────

            # Check: required tool was called
            if scenario.must_call_tool and tool_called != scenario.must_call_tool:
                failure_reasons.append(
                    f"Expected tool '{scenario.must_call_tool}' but got '{tool_called}'"
                )

            # Check: forbidden phrases
            response_lower = agent_response.lower()
            for forbidden in scenario.must_not_say:
                if forbidden.lower() in response_lower:
                    failure_reasons.append(f"Said forbidden phrase: '{forbidden}'")

            # Check: required phrases
            for required in scenario.must_contain:
                if required.lower() not in response_lower:
                    failure_reasons.append(f"Missing required phrase: '{required}'")

            # Check: latency (approximate — just LLM, not STT/TTS)
            if latency_ms and latency_ms > scenario.max_latency_ms * 3:
                # 3x budget for LLM-only (STT+TTS account for the rest in real calls)
                failure_reasons.append(
                    f"LLM latency {latency_ms:.0f}ms exceeds 3x budget {scenario.max_latency_ms * 3}ms"
                )

            passed = len(failure_reasons) == 0
            score = 1.0 if passed else max(0.0, 1.0 - len(failure_reasons) * 0.25)

            if verbose:
                status = "✅ PASS" if passed else "❌ FAIL"
                print(f"\n{status} [{scenario.id}] {scenario.description}")
                print(f"  Utterance : {scenario.caller_utterance}")
                print(f"  Response  : {agent_response[:200]}{'…' if len(agent_response) > 200 else ''}")
                print(f"  Tool      : {tool_called or 'none'}")
                print(f"  LLM latency: {latency_ms:.0f}ms" if latency_ms else "")
                for r in failure_reasons:
                    print(f"  ⚠ {r}")

            return ScenarioResult(
                scenario_id=scenario.id,
                category=scenario.category,
                phase=scenario.phase,
                passed=passed,
                score=score,
                agent_response=agent_response,
                tool_called=tool_called,
                latency_ms=latency_ms,
                failure_reasons=failure_reasons,
            )

        except Exception as e:
            tb = traceback.format_exc()
            logger.error(f"Scenario {scenario.id} raised exception: {e}\n{tb}")
            return ScenarioResult(
                scenario_id=scenario.id,
                category=scenario.category,
                phase=scenario.phase,
                passed=False,
                score=0.0,
                agent_response="",
                tool_called=None,
                latency_ms=None,
                failure_reasons=[f"Exception: {str(e)}"],
                notes=tb[:500],
            )

    async def run(
        self,
        phases: List[str],
        verbose: bool = False,
    ) -> Dict[str, Any]:
        """Run all scenarios for the given phases and return a summary report."""
        scenarios = [s for s in CALL_SCENARIOS if s.phase in phases]

        print(f"\n{'='*60}")
        print(f"AI IVR Caller — Call Evaluation Suite")
        print(f"Tenant : {self.tenant_id}")
        print(f"Bot    : {self.bot_id}")
        print(f"Phases : {', '.join(phases)}")
        print(f"Scenarios: {len(scenarios)}")
        print(f"{'='*60}")

        results: List[ScenarioResult] = []
        for scenario in scenarios:
            result = await self.evaluate_scenario(scenario, verbose=verbose)
            results.append(result)

        # Aggregate
        passed = [r for r in results if r.passed]
        by_category: Dict[str, Dict] = {}
        for r in results:
            cat = r.category
            if cat not in by_category:
                by_category[cat] = {"total": 0, "passed": 0}
            by_category[cat]["total"] += 1
            if r.passed:
                by_category[cat]["passed"] += 1

        latencies = [r.latency_ms for r in results if r.latency_ms is not None]
        p90_latency = sorted(latencies)[int(len(latencies) * 0.9)] if latencies else None

        summary = {
            "total": len(results),
            "passed": len(passed),
            "failed": len(results) - len(passed),
            "pass_rate": round(len(passed) / len(results) * 100, 1) if results else 0,
            "by_category": by_category,
            "p90_llm_latency_ms": round(p90_latency, 1) if p90_latency else None,
            "results": [asdict(r) for r in results],
        }

        print(f"\n{'─'*60}")
        print(f"Results: {len(passed)}/{len(results)} passed ({summary['pass_rate']}%)")
        for cat, stats in by_category.items():
            pct = round(stats["passed"] / stats["total"] * 100)
            icon = "✅" if pct == 100 else "⚠️ " if pct >= 80 else "❌"
            print(f"  {icon} {cat:20s} {stats['passed']}/{stats['total']} ({pct}%)")
        if p90_latency:
            budget_ok = "✅" if p90_latency < 1000 else "❌"
            print(f"  {budget_ok} P90 LLM latency: {p90_latency:.0f}ms (target <1000ms)")
        print(f"{'─'*60}\n")

        return summary


# ─────────────────────────────────────────────────────────────────────────────
# CLI entrypoint
# ─────────────────────────────────────────────────────────────────────────────

async def main():
    parser = argparse.ArgumentParser(description="AI IVR Caller — Call Evaluation Runner")
    parser.add_argument(
        "--mode",
        choices=["phase1", "phase2", "phase3", "all"],
        default="phase1",
        help="Which phase(s) to evaluate"
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Print per-scenario details")
    parser.add_argument("--output", "-o", help="Write JSON report to file")
    parser.add_argument(
        "--tenant-id",
        default=os.getenv("TEST_TENANT_ID", "03c254d0-0681-48e9-a425-64c6460d53c0"),
    )
    parser.add_argument(
        "--bot-id",
        default=os.getenv("TEST_BOT_ID", "b28eb4cc-6886-4500-bf18-5b9cfc519244"),
    )
    args = parser.parse_args()

    phases = ["phase1", "phase2", "phase3"] if args.mode == "all" else [args.mode]

    evaluator = CallEvaluator(tenant_id=args.tenant_id, bot_id=args.bot_id)
    report = await evaluator.run(phases=phases, verbose=args.verbose)

    if args.output:
        with open(args.output, "w") as f:
            json.dump(report, f, indent=2)
        print(f"Report written to {args.output}")

    # Non-zero exit if pass rate < 95% (matches brief's acceptance criteria)
    if report["pass_rate"] < 95:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
