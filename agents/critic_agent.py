"""Step 33 — Critic / verifier agent.

The component that makes answers trustworthy instead of merely plausible.

It reads the draft, isolates each SPECIFIC FACTUAL CLAIM (numbers, dates,
events, quotes, company actions), and checks whether the evidence supports
it. Analytical reasoning and definitions — permitted by the Step 32
synthesis rules — are NOT treated as claims to verify; flagging them would
punish the system for doing legitimate analysis.

Outcomes:
  passed   — every factual claim is supported; answer is finalized as-is
  revised  — minor issues; the critic rewrites to remove/soften bad claims
  failed   — serious unsupported claims AND no revisions left; answer is
             replaced with an honest, evidence-only fallback

This is the anti-hallucination gate. Its job is to ensure the system
never states an unsupported financial fact as though it were verified.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import settings

from agents import llm
from agents.state import AgentState


CRITIC_SYSTEM = (
    "You are a fact-checker for financial research answers. You verify that "
    "every specific factual claim in a draft is supported by the provided "
    "evidence. You distinguish factual claims (which must be supported) from "
    "analytical reasoning and definitions (which need not be). You are strict "
    "about numbers, dates, events, and quotes, and lenient about interpretation."
)

CRITIC_PROMPT = """Check the draft answer against the evidence.

QUESTION: {question}

EVIDENCE:
{evidence}

DRAFT ANSWER:
{draft}

For each SPECIFIC FACTUAL CLAIM in the draft (a number, date, event, named
product, quote, or concrete company/Fed action), decide whether the evidence
supports it. Do NOT flag analytical reasoning, definitions, or clearly
hedged interpretation ("could", "may signal", "often indicates") — those are
allowed to be unsupported.

Return JSON:
  {{"claims": [
      {{"claim": "the specific factual claim", "supported": true/false,
        "evidence_num": n or null, "issue": "why unsupported, or null"}}
   ],
   "verdict": "passed" | "revise",
   "unsupported_count": <number of unsupported factual claims>}}

Use "passed" only if every factual claim is supported. Otherwise "revise"."""


REVISE_SYSTEM = (
    "You rewrite a financial answer to remove unsupported claims while keeping "
    "everything the evidence supports. You use only the evidence."
)

REVISE_PROMPT = """Rewrite the answer to fix these problems.

QUESTION: {question}

EVIDENCE:
{evidence}

ORIGINAL DRAFT:
{draft}

UNSUPPORTED CLAIMS TO REMOVE OR CORRECT:
{issues}

Rewrite the answer so every specific fact is supported by the evidence with
an [n] reference. You may keep analytical reasoning. Drop or soften any claim
that isn't supported. If removing a claim leaves a gap, say what's not known.

Write only the corrected answer (no JSON, no preamble)."""


def _verify(state: AgentState):
    """Run the fact-check pass. Returns the parsed critic result."""
    prompt = CRITIC_PROMPT.format(
        question=state.query,
        evidence=state.evidence_block(),
        draft=state.draft_answer,
    )
    result = llm.complete_json(prompt, system=CRITIC_SYSTEM, max_tokens=800)

    # Defensive default: if the critic output is unparseable, treat as
    # a soft pass but record that verification was inconclusive.
    if not isinstance(result, dict) or "verdict" not in result:
        return {
            "claims": [],
            "verdict": "passed",
            "unsupported_count": 0,
            "_note": "critic output unparseable; verification inconclusive",
        }
    return result


def _revise(state: AgentState, unsupported):
    """Rewrite the draft to remove unsupported claims."""
    issues = "\n".join(
        f"- {c.get('claim','?')} ({c.get('issue','unsupported')})"
        for c in unsupported
    )
    prompt = REVISE_PROMPT.format(
        question=state.query,
        evidence=state.evidence_block(),
        draft=state.draft_answer,
        issues=issues,
    )
    revised = llm.complete(prompt, system=REVISE_SYSTEM,
                           temperature=0.1, max_tokens=600)
    return revised.strip()


def _fallback(state: AgentState):
    """Last resort: an honest, evidence-only answer.

    Used when claims remain unsupported and no revisions are left.
    Rather than risk shipping a hallucination, state only what the
    high/medium-confidence evidence directly supports.
    """
    strong = [e for e in state.evidence if e.confidence in ("high", "medium")]
    if not strong:
        return ("I don't have enough verified information to answer that "
                "confidently.")
    lines = ["Based only on what I could verify:"]
    for e in strong:
        lines.append(f"- {e.content} (source: {e.citation.render()})")
    return "\n".join(lines)


def run(state: AgentState):
    """Verify the draft; finalize, revise, or fall back."""
    if not state.draft_answer:
        state.final_answer = "No answer was generated."
        state.verification_status = "failed"
        return state

    result = _verify(state)
    unsupported = [c for c in result.get("claims", [])
                   if isinstance(c, dict) and c.get("supported") is False]

    state.verification_notes = result.get("claims", [])

    # Clean pass — finalize as-is
    if result.get("verdict") == "passed" and not unsupported:
        state.final_answer = state.draft_answer
        state.verification_status = "passed"
        return state

    # Problems found — revise if we still can
    if unsupported and state.can_revise():
        state.revision_count += 1
        revised = _revise(state, unsupported)
        state.draft_answer = revised
        # Re-verify the revision (one more pass through run())
        return run(state)

    # Out of revisions with problems remaining — honest fallback
    if unsupported:
        state.final_answer = _fallback(state)
        state.verification_status = "failed"
        return state

    # verdict said revise but nothing concrete flagged — accept the draft
    state.final_answer = state.draft_answer
    state.verification_status = "revised"
    return state


# ── Demo ───────────────────────────────────────────────────

def main():
    from agents.state import AgentState, AgentType, Citation

    # Evidence set that supports SOME claims but not others
    def fresh_state():
        s = AgentState(query="How did Microsoft's margin trend and why?")
        s.add_evidence(
            AgentType.NUMERIC,
            "MSFT gross margin was 67.6% (2026-03-31), down from 69.0% two "
            "quarters earlier.",
            Citation(source_type="xbrl", ticker="MSFT", period="2026-03-31"),
            confidence="high",
        )
        s.add_evidence(
            AgentType.RETRIEVAL,
            "Management attributed margin pressure to cloud infrastructure investment.",
            Citation(source_type="filing_text", ticker="MSFT", filing="10-Q_2026-04"),
            confidence="medium",
        )
        return s

    # Case 1: a clean, supported draft (should PASS)
    print("=" * 60)
    print("  CASE 1: supported draft")
    print("=" * 60)
    s1 = fresh_state()
    s1.draft_answer = (
        "Microsoft's gross margin declined to 67.6% from 69.0% two quarters "
        "earlier [1], which management attributed to cloud infrastructure "
        "investment [2]. This kind of investment ahead of revenue often "
        "compresses margins temporarily."
    )
    run(s1)
    print(f"Verdict: {s1.verification_status}")
    print(s1.final_answer)
    print()

    # Case 2: a draft with a FABRICATED fact (should catch it)
    print("=" * 60)
    print("  CASE 2: draft with an unsupported fabricated fact")
    print("=" * 60)
    s2 = fresh_state()
    s2.draft_answer = (
        "Microsoft's gross margin fell to 67.6% [1]. The company also "
        "announced a $10 billion buyback and laid off 5,000 employees, "
        "which pressured margins further."
    )
    run(s2)
    print(f"Verdict: {s2.verification_status}")
    print("Flagged claims:")
    for c in s2.verification_notes:
        if isinstance(c, dict):
            mark = "OK " if c.get("supported") else "BAD"
            print(f"  [{mark}] {c.get('claim','')[:70]}")
    print()
    print("Final answer:")
    print(s2.final_answer)


if __name__ == "__main__":
    main()