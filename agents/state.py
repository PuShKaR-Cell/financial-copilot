"""Step 27 — Shared agent state.

The single object that flows through the whole pipeline. Every agent
reads what it needs and writes its findings back. Nothing is passed
between agents except this — which is what keeps the orchestration in
Step 34 simple and makes citations traceable end to end.

Design choices:

  Evidence is append-only. Each specialist adds Evidence items; nobody
  edits another agent's evidence. The Synthesis agent reads all of it,
  the Critic checks claims against it. This makes the flow auditable —
  you can always see which agent produced which fact.

  Citations live on the evidence, not bolted on at the end. A number or
  quote enters the state already carrying its source (filing + page,
  table cell, transcript timestamp, or XBRL period). By the time the
  answer is written, every fact already knows where it came from.

  Plain dataclasses, not a framework type. LangGraph (Step 34) can wrap
  this, but the state itself stays framework-agnostic so it's testable
  in isolation and portable if the orchestration layer ever changes.
"""

from dataclasses import dataclass, field, asdict
from typing import Optional, Any
from enum import Enum


class AgentType(str, Enum):
    """Which specialist a sub-task is routed to."""
    RETRIEVAL = "retrieval"      # filings: text, pages, tables
    NUMERIC = "numeric"          # XBRL facts, ratios, forecasts
    SENTIMENT = "sentiment"      # earnings/FOMC transcript tone


@dataclass
class Citation:
    """Where a single piece of evidence came from.

    One of these per fact. The `ref` fields are populated according to
    source_type, so the final answer can render "10-Q p.34" or
    "call at 14:22" or "XBRL, period ending 2025-06-30".
    """
    source_type: str                      # filing_text | filing_table | transcript | xbrl | forecast
    ticker: Optional[str] = None
    filing: Optional[str] = None
    page: Optional[int] = None
    table_index: Optional[int] = None
    period: Optional[str] = None
    timestamp: Optional[str] = None       # transcript "MM:SS"
    speaker: Optional[str] = None
    detail: Optional[str] = None          # freeform, e.g. XBRL tag or method

    def render(self):
        """A short human-readable citation string for the final answer."""
        if self.source_type == "filing_text":
            loc = f"{self.filing}" + (f", p.{self.page}" if self.page else "")
            return f"{self.ticker} {loc}"
        if self.source_type == "filing_table":
            return f"{self.ticker} {self.filing} (table {self.table_index})"
        if self.source_type == "transcript":
            who = self.speaker or "speaker"
            at = f" at {self.timestamp}" if self.timestamp else ""
            return f"{who}, {self.period}{at}"
        if self.source_type == "xbrl":
            return f"{self.ticker} XBRL, period ending {self.period}"
        if self.source_type == "forecast":
            return f"{self.ticker} forecast ({self.detail})"
        return self.source_type


@dataclass
class Evidence:
    """One finding produced by a specialist agent.

    `content` is the fact in words (what Synthesis reads and the Critic
    verifies). `citation` is where it came from. `agent` records who
    produced it, for auditability.
    """
    agent: AgentType
    content: str
    citation: Citation
    confidence: str = "medium"            # low | medium | high
    raw: Any = None                       # underlying structured result, if useful


@dataclass
class SubTask:
    """One unit of work the Planner carved out of the user's question."""
    description: str
    agent: AgentType
    ticker: Optional[str] = None
    metric: Optional[str] = None          # hint for the numeric agent
    done: bool = False


@dataclass
class AgentState:
    """The object that flows through the entire pipeline."""
    query: str

    sub_tasks: list = field(default_factory=list)          # list[SubTask]
    evidence: list = field(default_factory=list)           # list[Evidence]

    draft_answer: Optional[str] = None
    final_answer: Optional[str] = None

    # Set by the Critic (Step 33)
    verification_status: Optional[str] = None              # passed | failed | revised
    verification_notes: list = field(default_factory=list)

    # Orchestration guardrails (Step 34)
    revision_count: int = 0
    max_revisions: int = 2
    errors: list = field(default_factory=list)

    # ── evidence helpers ──────────────────────────────────

    def add_evidence(self, agent, content, citation, confidence="medium", raw=None):
        """Append one finding. Evidence is never overwritten."""
        self.evidence.append(
            Evidence(agent=agent, content=content, citation=citation,
                     confidence=confidence, raw=raw)
        )

    def evidence_from(self, agent):
        """All evidence produced by one specialist."""
        return [e for e in self.evidence if e.agent == agent]

    def evidence_block(self):
        """Numbered evidence list for the Synthesis and Critic prompts.

        Every item is numbered so the Critic can refer to "evidence 3"
        and so the final answer can attach [n] style references.
        """
        lines = []
        for i, e in enumerate(self.evidence, 1):
            lines.append(f"[{i}] ({e.agent.value}, {e.confidence}) "
                         f"{e.content}  — source: {e.citation.render()}")
        return "\n".join(lines) if lines else "(no evidence gathered)"

    def citations_list(self):
        """De-duplicated rendered citations, for an answer's source list."""
        seen = []
        for e in self.evidence:
            r = e.citation.render()
            if r not in seen:
                seen.append(r)
        return seen

    # ── task helpers ──────────────────────────────────────

    def add_sub_task(self, description, agent, ticker=None, metric=None):
        self.sub_tasks.append(
            SubTask(description=description, agent=agent,
                    ticker=ticker, metric=metric)
        )

    def tasks_for(self, agent):
        return [t for t in self.sub_tasks if t.agent == agent]

    def can_revise(self):
        """Whether the Critic is still allowed to send the draft back."""
        return self.revision_count < self.max_revisions

    def summary(self):
        """One-line state summary, for logging/tracing."""
        return (f"query={self.query[:40]!r} "
                f"tasks={len(self.sub_tasks)} evidence={len(self.evidence)} "
                f"status={self.verification_status} rev={self.revision_count}")


# ── Demo ───────────────────────────────────────────────────

def main():
    # Build a state by hand to show the shape the agents will produce
    state = AgentState(query="How did MSFT's gross margin trend, and what was the tone on rates?")

    state.add_sub_task("Get MSFT gross margin over 4 quarters",
                       AgentType.NUMERIC, ticker="MSFT", metric="gross_margin")
    state.add_sub_task("Find management commentary on margins",
                       AgentType.RETRIEVAL, ticker="MSFT")
    state.add_sub_task("Assess tone on interest rates",
                       AgentType.SENTIMENT)

    print("Sub-tasks:")
    for t in state.sub_tasks:
        print(f"  [{t.agent.value:9}] {t.description}")
    print()

    # Simulate each specialist adding evidence with citations
    state.add_evidence(
        AgentType.NUMERIC,
        "MSFT gross margin was 67.6% (Q3'26), down from 69.0% two quarters earlier.",
        Citation(source_type="xbrl", ticker="MSFT", period="2026-03-31"),
        confidence="high",
    )
    state.add_evidence(
        AgentType.RETRIEVAL,
        "Management attributed margin pressure to cloud infrastructure buildout.",
        Citation(source_type="filing_text", ticker="MSFT",
                 filing="10-Q_2026-04", page=34),
        confidence="medium",
    )
    state.add_evidence(
        AgentType.SENTIMENT,
        "Chair Powell's tone on rates grew less negative into 2026 (2.8% negative).",
        Citation(source_type="transcript", speaker="POWELL",
                 period="2026-04-29", timestamp="12:40"),
        confidence="medium",
    )

    print("Evidence block (what Synthesis and Critic see):")
    print(state.evidence_block())
    print()

    print("Citations list (for the answer's sources):")
    for c in state.citations_list():
        print(f"  - {c}")
    print()

    print("State summary:", state.summary())


if __name__ == "__main__":
    main()