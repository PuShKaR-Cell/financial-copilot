"""Step 32 — Synthesis agent.

Reads every piece of evidence the specialists gathered and writes one
coherent answer to the user's original question.

The hard constraint: synthesis may use ONLY the evidence in state. It
does not add outside knowledge, does not smooth over gaps with plausible
filler, and does not upgrade a "low confidence" finding into a firm
claim. If the evidence is thin or conflicting, the answer says so.

That discipline is what makes verification possible: because every
sentence is supposed to trace to a numbered piece of evidence, the
Critic (Step 33) can check each claim against its source.

The output attaches [n] references matching the evidence block, so the
final answer is auditable rather than a black box.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import settings

from agents import llm
from agents.state import AgentState


SYNTHESIS_SYSTEM = (
    "You are a financial research analyst writing a grounded answer. Every "
    "specific fact — numbers, dates, events, quotes, company actions — must "
    "come from the numbered evidence, with an [n] reference. You MAY add "
    "analytical reasoning, definitions, and framing from general knowledge, "
    "but you must NOT introduce specific facts that aren't in the evidence. "
    "When evidence is limited or conflicting, you say so plainly."
)

SYNTHESIS_PROMPT = """Answer the user's question using ONLY the evidence below.

QUESTION: {question}

EVIDENCE:
{evidence}

Instructions:
- Write a clear, direct answer in an analyst's voice (2-5 sentences,
  more only if the question has several parts).
- Attach [n] references to every SPECIFIC FACT, matching the evidence numbers.
- - You MAY add unlabeled analytical reasoning, definitions, or framing
  (e.g. "margin compression often signals pricing pressure" or explaining
  what a metric means) — these are interpretation, not fact claims.
- You may NOT introduce any specific fact not in the evidence: no numbers,
  dates, events, named products, or quotes that lack an [n] reference.
- Respect confidence levels: don't state a "low" confidence finding as a
  hard fact. For low-confidence forecasts, include the caveat.
- If the evidence doesn't fully answer the question, say what's missing.

Write only the answer text (no JSON, no preamble)."""


def run(state: AgentState):
    """Compose the draft answer from gathered evidence."""
    evidence_block = state.evidence_block()

    # Nothing to synthesize
    if not state.evidence:
        state.draft_answer = (
            "I couldn't gather enough information to answer that question."
        )
        return state

    prompt = SYNTHESIS_PROMPT.format(
        question=state.query,
        evidence=evidence_block,
    )

    draft = llm.complete(
        prompt,
        system=SYNTHESIS_SYSTEM,
        temperature=0.2,     # a little room for readable prose, still grounded
        max_tokens=600,
    )

    state.draft_answer = draft.strip()
    return state


# ── Demo ───────────────────────────────────────────────────

def main():
    from agents.state import AgentState, AgentType, Citation

    # Build a realistic multi-source evidence set by hand, so we test
    # synthesis in isolation from the (slow) specialist agents.
    state = AgentState(
               query="Why might Microsoft's gross margin be declining, and what does "
              "the Fed's tone suggest about the rate environment ahead?"
    )

    state.add_evidence(
        AgentType.NUMERIC,
        "MSFT gross_profit/revenue: latest 67.6% (2026-03-31), down from "
        "69.0% two quarters earlier — a gradual decline.",
        Citation(source_type="xbrl", ticker="MSFT", period="2026-03-31"),
        confidence="high",
    )
    state.add_evidence(
        AgentType.RETRIEVAL,
        "Management attributed margin pressure to heavy cloud infrastructure "
        "investment ahead of demand.",
        Citation(source_type="filing_text", ticker="MSFT",
                 filing="10-Q_2026-04", detail="mda"),
        confidence="medium",
    )
    state.add_evidence(
        AgentType.SENTIMENT,
        "The Fed Chair's tone on rates grew less negative into 2026 "
        "(2.8% negative in April vs 24% the prior December), with lower hedging.",
        Citation(source_type="transcript", speaker="POWELL",
                 period="2026-04-29", timestamp="12:40"),
        confidence="medium",
    )

    print("Evidence going in:")
    print(state.evidence_block())
    print()
    print("=" * 60)
    print("  Synthesizing...")
    print("=" * 60)
    print()

    run(state)
    print(state.draft_answer)


if __name__ == "__main__":
    main()