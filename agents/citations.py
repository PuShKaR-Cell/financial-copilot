"""Step 35 — Citation resolution and verification.

Citations are gathered by every agent (Step 27) and flow through
synthesis and the critic into the final answer. This module closes the
last gap: mapping the [n] references in the answer text back to the
actual Citation objects, so the API and UI (Phase 6) can render each
reference as a clickable source rather than a bare number.

It also provides a verification pass used in development and in the
eval harness (Phase 7): does every [n] in the answer point to a real
piece of evidence, and does every factual claim carry a reference?
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import settings

from agents.state import AgentState


REF_PATTERN = re.compile(r"\[(\d+)\]")


def resolve_references(state: AgentState):
    """Map each [n] in the final answer to its evidence + citation.

    Returns a list of {ref, content, citation, source_type} for the
    references actually used in the answer, in order of first appearance.
    """
    answer = state.final_answer or ""
    used_refs = []
    seen = set()

    for match in REF_PATTERN.finditer(answer):
        n = int(match.group(1))
        if n in seen:
            continue
        seen.add(n)
        # Evidence is 1-indexed in the numbered block
        if 1 <= n <= len(state.evidence):
            e = state.evidence[n - 1]
            used_refs.append({
                "ref": n,
                "content": e.content,
                "citation": e.citation.render(),
                "source_type": e.citation.source_type,
                "confidence": e.confidence,
            })

    return used_refs


def verify_citations(state: AgentState):
    """Development/eval check: are the answer's references valid?

    Returns a report dict:
      total_refs       — how many distinct [n] appear in the answer
      valid_refs       — how many point to real evidence
      dangling_refs    — [n] that point to nothing (a bug if > 0)
      unused_evidence  — evidence items never referenced
      has_any_citation — whether the answer cites anything at all
    """
    answer = state.final_answer or ""
    refs = [int(m.group(1)) for m in REF_PATTERN.finditer(answer)]
    distinct = set(refs)

    valid = {n for n in distinct if 1 <= n <= len(state.evidence)}
    dangling = distinct - valid
    referenced = valid
    all_evidence_nums = set(range(1, len(state.evidence) + 1))
    unused = all_evidence_nums - referenced

    return {
        "total_refs": len(distinct),
        "valid_refs": len(valid),
        "dangling_refs": sorted(dangling),
        "unused_evidence": sorted(unused),
        "has_any_citation": len(valid) > 0,
    }


def format_answer_with_sources(state: AgentState):
    """Produce a display-ready answer: text + a numbered source list.

    This is what the API will return and the UI will render.
    """
    answer = state.final_answer or "(no answer)"
    refs = resolve_references(state)

    lines = [answer, ""]
    if refs:
        lines.append("Sources:")
        for r in refs:
            lines.append(f"  [{r['ref']}] {r['citation']}  "
                         f"({r['source_type']}, {r['confidence']} confidence)")
    else:
        # The fallback answers (critic failure) list sources inline
        # rather than via [n]; show the deduped citation list instead.
        cites = state.citations_list()
        if cites:
            lines.append("Sources:")
            for c in cites:
                lines.append(f"  - {c}")

    return "\n".join(lines)


# ── Demo ───────────────────────────────────────────────────

def main():
    from agents.state import AgentState, AgentType, Citation

    state = AgentState(query="How did MSFT margin trend and why?")
    state.add_evidence(
        AgentType.NUMERIC,
        "MSFT gross margin was 67.6% (2026-03-31), down from 69.0%.",
        Citation(source_type="xbrl", ticker="MSFT", period="2026-03-31"),
        confidence="high",
    )
    state.add_evidence(
        AgentType.RETRIEVAL,
        "Management cited cloud infrastructure investment.",
        Citation(source_type="filing_text", ticker="MSFT", filing="10-Q_2026-04"),
        confidence="medium",
    )
    state.final_answer = (
        "Microsoft's gross margin declined to 67.6% from 69.0% [1], which "
        "management attributed to cloud infrastructure investment [2]. Such "
        "investment often compresses margins temporarily before revenue catches up."
    )

    print("=== Reference resolution ===")
    for r in resolve_references(state):
        print(f"  [{r['ref']}] -> {r['citation']} ({r['source_type']})")
    print()

    print("=== Verification report ===")
    report = verify_citations(state)
    for k, v in report.items():
        print(f"  {k}: {v}")
    print()

    print("=== Display-ready answer ===")
    print(format_answer_with_sources(state))


if __name__ == "__main__":
    main()