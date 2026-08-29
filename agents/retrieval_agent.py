"""Step 29 — Retrieval agent.

Answers "what do the filings say about X" sub-tasks.

Flow:
  1. Hybrid retrieve: vector search over text_chunks (and table_chunks),
     filtered by ticker and — when useful — by section label (Step 16)
  2. Rerank the candidates with the cross-encoder (Step 17)
  3. Have the LLM read the top passages and extract grounded findings
  4. Write each finding into state as Evidence with a filing citation

The agent returns evidence, not raw chunks. It reads the passages and
states what they say, attaching a citation to each claim, so the
Synthesis and Critic agents downstream have something verifiable.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import settings

from agents import llm
from agents.state import AgentState, AgentType, Citation
from processing import reranker


# Section hints: nudge retrieval toward the right part of the filing
# when the sub-task obviously concerns one. Soft — we don't hard-filter,
# since Step 16's labels are ~60% accurate on previews.
SECTION_HINTS = {
    "risk": "risk_factors",
    "risks": "risk_factors",
    "legal": "legal",
    "lawsuit": "legal",
    "litigation": "legal",
    "compensation": "compensation",
    "management discussion": "mda",
    "outlook": "mda",
}


def _pick_section(description):
    d = description.lower()
    for keyword, section in SECTION_HINTS.items():
        if keyword in d:
            return section
    return None


RETRIEVAL_SYSTEM = (
    "You extract factual findings from SEC filing excerpts. You report "
    "only what the excerpts actually say. You never invent details. If the "
    "excerpts don't address the question, you say so plainly."
)

RETRIEVAL_PROMPT = """Answer the sub-task using ONLY these filing excerpts.

SUB-TASK: {task}

EXCERPTS:
{excerpts}

Extract up to 3 factual findings that address the sub-task. For each,
quote or closely paraphrase what the filing says, and note which excerpt
number it came from.

Return JSON: a list of objects, each with:
  "finding": one sentence stating what the filing says
  "excerpt": the excerpt number [1-N] it came from
  "confidence": "high" if directly stated, "medium" if implied

If none of the excerpts address the sub-task, return an empty list []."""


def _retrieve(task, top_k=5):
    """Hybrid retrieve + rerank for one sub-task.

    Searches text_chunks primarily; the reranker (Step 17) already
    handles candidate widening and cross-encoder scoring.
    """
    section = _pick_section(task.description)

    # Reranker.search does: vector search -> cross-encoder rerank
    results = reranker.search(
        task.description,
        collection="text_chunks",
        top_k=top_k,
        ticker=task.ticker,
        section=section,
    )
    return results


def _format_excerpts(results):
    lines = []
    for i, r in enumerate(results, 1):
        meta = f"{r.get('ticker','?')} / {r.get('filing','?')}"
        sec = r.get("section")
        if sec:
            meta += f" / {sec}"
        lines.append(f"[{i}] ({meta})\n{r.get('text','')}")
    return "\n\n".join(lines)


def run(state: AgentState):
    """Execute all retrieval sub-tasks, writing evidence into state."""
    tasks = state.tasks_for(AgentType.RETRIEVAL)

    for task in tasks:
        results = _retrieve(task, top_k=5)

        if not results:
            state.add_evidence(
                AgentType.RETRIEVAL,
                f"No filing passages found for: {task.description}",
                Citation(source_type="filing_text", ticker=task.ticker),
                confidence="low",
            )
            task.done = True
            continue

        excerpts = _format_excerpts(results)
        prompt = RETRIEVAL_PROMPT.format(task=task.description, excerpts=excerpts)
        findings = llm.complete_json(prompt, system=RETRIEVAL_SYSTEM, max_tokens=600)

        if not isinstance(findings, list):
            findings = []

        if not findings:
            state.add_evidence(
                AgentType.RETRIEVAL,
                f"Filings retrieved but none directly addressed: {task.description}",
                Citation(source_type="filing_text", ticker=task.ticker),
                confidence="low",
            )
            task.done = True
            continue

        for f in findings:
            if not isinstance(f, dict) or "finding" not in f:
                continue
            # Map the cited excerpt back to its source for the citation
            idx = f.get("excerpt")
            src = None
            if isinstance(idx, int) and 1 <= idx <= len(results):
                src = results[idx - 1]
            elif isinstance(idx, str) and idx.strip("[]").isdigit():
                n = int(idx.strip("[]"))
                if 1 <= n <= len(results):
                    src = results[n - 1]

            citation = Citation(
                source_type="filing_text",
                ticker=(src or {}).get("ticker", task.ticker),
                filing=(src or {}).get("filing"),
                detail=(src or {}).get("section"),
            )
            state.add_evidence(
                AgentType.RETRIEVAL,
                f["finding"],
                citation,
                confidence=f.get("confidence", "medium"),
            )

        task.done = True

    return state


# ── Demo ───────────────────────────────────────────────────

def main():
    from agents.state import AgentState

    # Build a state with retrieval tasks directly (skip the planner
    # here so we test retrieval in isolation)
    state = AgentState(query="test")
    state.add_sub_task(
        "Identify Snowflake's biggest risk factors",
        AgentType.RETRIEVAL, ticker="SNOW",
    )
    state.add_sub_task(
        "What does Palo Alto Networks say about competition?",
        AgentType.RETRIEVAL, ticker="PANW",
    )

    print("Running retrieval agent on 2 sub-tasks...")
    print("(hybrid search + rerank + LLM extraction — slow on CPU)")
    print()

    run(state)

    for e in state.evidence_from(AgentType.RETRIEVAL):
        print(f"  [{e.confidence:6}] {e.content}")
        print(f"           source: {e.citation.render()}")
        print()


if __name__ == "__main__":
    main()