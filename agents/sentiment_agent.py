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

It reports representative quotes WITH their tone and timestamp, so the
final answer can say "the Chair sounded cautious (2.1 hedging markers
per 100 words) — 'we remain data dependent' (at 14:22)".
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

_encoder = None


def get_encoder():
    global _encoder
    if _encoder is None:
        _encoder = SentenceTransformer(TEXT_MODEL)
    return _encoder


def _retrieve(description, role=None, limit=6):
    """Semantic search over transcript segments, optionally by role.

    role='chair' restricts to the Fed Chair / management, which is
    usually what "how did management sound" actually means — press
    questions are noise for a tone read.
    """
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

    # Interpret hedging level qualitatively
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
        ts_str = f" @{ts}" if ts else ""
        event = s.get("event_id", "")
        preview = s.get("preview", "")[:220]
        lines.append(f"[{i}] {who} ({tone}{ts_str}, {event}): {preview}")
    return "\n\n".join(lines)


def run(state: AgentState):
    """Execute all sentiment sub-tasks, writing evidence into state."""
    tasks = state.tasks_for(AgentType.SENTIMENT)

    for task in tasks:
        # "management"/"chair"/"fed" tone questions -> restrict to chair role
        desc_l = task.description.lower()
        role = "chair" if any(w in desc_l for w in
                              ("management", "chair", "fed", "powell", "warsh", "ceo", "cfo")) else None

        segments = _retrieve(task.description, role=role, limit=6)

        if not segments:
            # Retry without the role filter before giving up
            segments = _retrieve(task.description, role=None, limit=6)

        if not segments:
            state.add_evidence(
                AgentType.SENTIMENT,
                f"No transcript segments found for: {task.description}",
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
            # Fall back to reporting the raw metrics even if the LLM stalls
            state.add_evidence(
                AgentType.SENTIMENT,
                f"Tone was predominantly {tone['dominant_sentiment']} with "
                f"{tone['hedge_desc']} ({tone['avg_hedging']} markers/100 words) "
                f"across {tone['n']} relevant segments.",
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

            citation = Citation(
                source_type="transcript",
                speaker=(src or segments[0]).get("speaker_normalized")
                        or (src or segments[0]).get("speaker"),
                period=(src or segments[0]).get("event_id"),
                timestamp=(src or segments[0]).get("timestamp"),
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
    state.add_sub_task("Assess the tone on interest rate decisions",
                       AgentType.SENTIMENT)

    print("Running sentiment agent on 2 sub-tasks...")
    print()

    run(state)

    for e in state.evidence_from(AgentType.SENTIMENT):
        print(f"  [{e.confidence:6}] {e.content}")
        print(f"           source: {e.citation.render()}")
        print()


if __name__ == "__main__":
    main()
