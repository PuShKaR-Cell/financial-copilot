"""Step 31 — Sentiment agent.

Answers "how did they sound" sub-tasks from the transcript corpus.

Each transcript segment already carries, from Phase 3:
  - FinBERT sentiment + score
  - hedging density (uncertainty markers per 100 words)
  - speaker + normalized role (chair vs press)

So the agent does two things a text-only search can't:
  1. Retrieves segments semantically relevant to the sub-task
  2. Aggregates the attached tone metrics into a quantified read
     ("mostly neutral, elevated hedging"), not just a vibe

Scope note: the only transcript corpus in this system is FOMC (Federal
Reserve) press conferences. A tone question about a specific company has
no matching audio data, so the agent says so rather than substituting Fed
commentary for company management — a correctness guard added after an
end-to-end run surfaced exactly that confusion.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import settings

from sentence_transformers import SentenceTransformer
from qdrant_client import QdrantClient

from agents import llm
from agents.state import AgentState, AgentType, Citation

COLLECTION = "call_transcripts"
TEXT_MODEL = "all-MiniLM-L6-v2"

# Topic words indicating the sub-task is actually about the Fed / macro,
# in which case the FOMC corpus is the correct source even if a ticker
# was attached by the planner.
FED_TERMS = (
    "fed", "fomc", "powell", "warsh", "chair", "federal reserve",
    "rate", "rates", "inflation", "monetary", "interest",
)

_encoder = None


def get_encoder():
    global _encoder
    if _encoder is None:
        _encoder = SentenceTransformer(TEXT_MODEL)
    return _encoder


def _retrieve(description, role=None, limit=6):
    """Semantic search over transcript segments, optionally by role."""
    client = QdrantClient(url=settings.qdrant_url)
    vector = get_encoder().encode(description).tolist()

    query_filter = None
    if role:
        query_filter = {"must": [{"key": "role", "match": {"value": role}}]}

    response = client.query_points(
        collection_name=COLLECTION,
        query=vector,
        limit=limit,
        query_filter=query_filter,
        with_payload=True,
    )
    return [p.payload for p in response.points]


def _aggregate_tone(segments):
    """Summarize the tone metrics across retrieved segments."""
    if not segments:
        return None

    sentiments = [s.get("sentiment") for s in segments if s.get("sentiment")]
    hedges = [s.get("hedging_density") for s in segments
              if s.get("hedging_density") is not None]

    counts = {}
    for s in sentiments:
        counts[s] = counts.get(s, 0) + 1

    dominant = max(counts, key=counts.get) if counts else "unknown"
    avg_hedge = round(sum(hedges) / len(hedges), 2) if hedges else None

    hedge_desc = ""
    if avg_hedge is not None:
        if avg_hedge >= 2.0:
            hedge_desc = "elevated hedging"
        elif avg_hedge >= 1.0:
            hedge_desc = "moderate hedging"
        else:
            hedge_desc = "low hedging"

    return {
        "dominant_sentiment": dominant,
        "sentiment_counts": counts,
        "avg_hedging": avg_hedge,
        "hedge_desc": hedge_desc,
        "n": len(segments),
    }


SENTIMENT_SYSTEM = (
    "You summarize the tone of financial/policy remarks from transcript "
    "excerpts that already have sentiment and hedging metrics attached. "
    "You report what was said and how it was said, grounded in the excerpts."
)

SENTIMENT_PROMPT = """Summarize the tone for this sub-task using the excerpts.

SUB-TASK: {task}

MEASURED TONE (computed, not your judgment):
  dominant sentiment: {dominant}
  sentiment breakdown: {counts}
  average hedging: {hedging} per 100 words ({hedge_desc})
  based on {n} relevant segments

EXCERPTS (each with its speaker, tone, and timestamp):
{excerpts}

Write 1-2 findings about the tone. Ground them in the measured metrics
above and quote a short representative phrase. Note the speaker.

Return JSON: a list of objects, each with:
  "finding": one sentence on the tone, citing a metric and/or short quote
  "excerpt": the excerpt number the quote came from
  "confidence": "high" if metrics and quotes agree, else "medium"

If the excerpts don't address the sub-task, return []."""


def _format_excerpts(segments):
    lines = []
    for i, s in enumerate(segments, 1):
        who = s.get("speaker_normalized") or s.get("speaker") or "speaker"
        tone = s.get("sentiment", "?")
        ts = s.get("timestamp", "")
        ts_str = " @" + ts if ts else ""
        event = s.get("event_id", "")
        preview = s.get("preview", "")[:220]
        lines.append("[" + str(i) + "] " + who + " (" + str(tone) + ts_str
                     + ", " + str(event) + "): " + preview)
    return "\n\n".join(lines)


def run(state: AgentState):
    """Execute all sentiment sub-tasks, writing evidence into state."""
    tasks = state.tasks_for(AgentType.SENTIMENT)

    for task in tasks:
        desc_l = task.description.lower()
        is_fed_topic = any(term in desc_l for term in FED_TERMS)

        # Company-specific tone question with no matching corpus:
        # be honest about the data gap rather than substituting Fed data.
        if task.ticker and not is_fed_topic:
            state.add_evidence(
                AgentType.SENTIMENT,
                "No earnings-call transcript data is available for "
                + task.ticker + " in this system; tone analysis is limited to "
                "Federal Reserve / FOMC communications.",
                Citation(source_type="transcript", ticker=task.ticker),
                confidence="low",
            )
            task.done = True
            continue

        # Fed/macro tone question: restrict to the chair when the phrasing
        # points at the decision-maker rather than the press pool.
        role = "chair" if any(w in desc_l for w in
                              ("chair", "fed", "powell", "warsh", "management",
                               "ceo", "cfo")) else None

        segments = _retrieve(task.description, role=role, limit=6)
        if not segments:
            segments = _retrieve(task.description, role=None, limit=6)

        if not segments:
            state.add_evidence(
                AgentType.SENTIMENT,
                "No transcript segments found for: " + task.description,
                Citation(source_type="transcript"),
                confidence="low",
            )
            task.done = True
            continue

        tone = _aggregate_tone(segments)
        excerpts = _format_excerpts(segments)

        prompt = SENTIMENT_PROMPT.format(
            task=task.description,
            dominant=tone["dominant_sentiment"],
            counts=tone["sentiment_counts"],
            hedging=tone["avg_hedging"],
            hedge_desc=tone["hedge_desc"],
            n=tone["n"],
            excerpts=excerpts,
        )
        findings = llm.complete_json(prompt, system=SENTIMENT_SYSTEM, max_tokens=500)

        if not isinstance(findings, list) or not findings:
            state.add_evidence(
                AgentType.SENTIMENT,
                "Tone was predominantly " + tone["dominant_sentiment"]
                + " with " + tone["hedge_desc"] + " ("
                + str(tone["avg_hedging"]) + " markers/100 words) across "
                + str(tone["n"]) + " relevant segments.",
                Citation(source_type="transcript",
                         speaker=segments[0].get("speaker_normalized"),
                         period=segments[0].get("event_id"),
                         timestamp=segments[0].get("timestamp")),
                confidence="medium",
            )
            task.done = True
            continue

        for f in findings:
            if not isinstance(f, dict) or "finding" not in f:
                continue
            idx = f.get("excerpt")
            src = None
            if isinstance(idx, int) and 1 <= idx <= len(segments):
                src = segments[idx - 1]

            base = src or segments[0]
            citation = Citation(
                source_type="transcript",
                speaker=base.get("speaker_normalized") or base.get("speaker"),
                period=base.get("event_id"),
                timestamp=base.get("timestamp"),
            )
            state.add_evidence(
                AgentType.SENTIMENT,
                f["finding"],
                citation,
                confidence=f.get("confidence", "medium"),
            )

        task.done = True

    return state


# ── Demo ───────────────────────────────────────────────────

def main():
    state = AgentState(query="test")
    state.add_sub_task("How did the Fed Chair sound about inflation?",
                       AgentType.SENTIMENT)
    state.add_sub_task("Assess the tone of Microsoft's management",
                       AgentType.SENTIMENT, ticker="MSFT")

    print("Running sentiment agent on 2 sub-tasks...")
    print("(one Fed question, one company question with no matching data)")
    print()

    run(state)

    for e in state.evidence_from(AgentType.SENTIMENT):
        print("  [" + e.confidence + "] " + e.content)
        print("           source: " + e.citation.render())
        print()


if __name__ == "__main__":
    main()