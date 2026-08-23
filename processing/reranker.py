"""Step 17 — Cross-encoder reranking.

Two-stage retrieval:
  Stage 1 (bi-encoder)   — Qdrant vector search returns ~50 candidates fast
  Stage 2 (cross-encoder) — rescores those 50 by reading query+doc together,
                            returns the best 5

Why both: a bi-encoder embeds the query and the document separately, so
it never compares them directly — fast but blunt. A cross-encoder reads
the pair together and scores the actual match, which is far more accurate
but too slow to run over a whole collection. Retrieving wide then
reranking narrow gets you both.

This module is imported by the Retrieval Agent (Step 29); running it
directly executes a demo query so you can see the reordering happen.
"""

import os
import sys
from sentence_transformers import CrossEncoder, SentenceTransformer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import settings

from qdrant_client import QdrantClient

# ── Constants ──────────────────────────────────────────────

RERANK_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"  # ~80MB, CPU-friendly
TEXT_MODEL = "all-MiniLM-L6-v2"                        # must match Step 15

CANDIDATES = 50   # how many to pull from Qdrant before reranking
TOP_K = 5         # how many to return after reranking

# Module-level model cache so repeated calls don't reload from disk
_cross_encoder = None
_bi_encoder = None


def get_cross_encoder():
    global _cross_encoder
    if _cross_encoder is None:
        _cross_encoder = CrossEncoder(RERANK_MODEL)
    return _cross_encoder


def get_bi_encoder():
    global _bi_encoder
    if _bi_encoder is None:
        _bi_encoder = SentenceTransformer(TEXT_MODEL)
    return _bi_encoder


def get_qdrant():
    return QdrantClient(url=settings.qdrant_url)


def retrieve_candidates(query, collection="text_chunks", limit=CANDIDATES,
                        ticker=None, section=None):
    """Stage 1 — fast vector search in Qdrant.

    Optional filters narrow the search before it runs:
      ticker  — restrict to one company
      section — restrict to one filing section (from Step 16)
    """
    client = get_qdrant()
    model = get_bi_encoder()

    query_vector = model.encode(query).tolist()

    # Build filter conditions if any were requested
    query_filter = None
    conditions = []
    if ticker:
        conditions.append({"key": "ticker", "match": {"value": ticker}})
    if section:
        conditions.append({"key": "section", "match": {"value": section}})
    if conditions:
        query_filter = {"must": conditions}

        # qdrant-client >= 1.10 replaced .search() with .query_points()
    response = client.query_points(
        collection_name=collection,
        query=query_vector,
        limit=limit,
        query_filter=query_filter,
        with_payload=True,
    )
    results = response.points
    return [
        {
            "id": r.id,
            "vector_score": r.score,
            "text": r.payload.get("preview", ""),
            "ticker": r.payload.get("ticker"),
            "filing": r.payload.get("filing"),
            "section": r.payload.get("section"),
            "payload": r.payload,
        }
        for r in results
    ]


def rerank(query, candidates, top_k=TOP_K):
    """Stage 2 — rescore candidates with the cross-encoder.

    Returns the top_k candidates, each with a `rerank_score` added
    and `original_rank` recording where it sat before reranking.
    """
    if not candidates:
        return []

    model = get_cross_encoder()

    # Record pre-rerank position so we can show what moved
    for i, c in enumerate(candidates):
        c["original_rank"] = i + 1

    pairs = [(query, c["text"]) for c in candidates]
    scores = model.predict(pairs)

    for c, score in zip(candidates, scores):
        c["rerank_score"] = float(score)

    ranked = sorted(candidates, key=lambda c: c["rerank_score"], reverse=True)
    return ranked[:top_k]


def search(query, collection="text_chunks", top_k=TOP_K,
           ticker=None, section=None):
    """Full two-stage retrieval — the function the Retrieval Agent calls."""
    candidates = retrieve_candidates(
        query, collection=collection, ticker=ticker, section=section
    )
    return rerank(query, candidates, top_k=top_k)


# ── Demo ───────────────────────────────────────────────────

def main():
    query = "supply chain disruption and component shortages"

    print(f"Query: {query!r}")
    print(f"Retrieving {CANDIDATES} candidates, reranking to top {TOP_K}")
    print()

    print("Loading models...")
    candidates = retrieve_candidates(query)
    print(f"Stage 1 returned {len(candidates)} candidates")
    print()

    print("── Before reranking (vector similarity order) ──")
    for c in candidates[:TOP_K]:
        print(f"  {c['ticker']:6} [{c.get('section') or '-':20}] "
              f"score={c['vector_score']:.3f}  {c['text'][:65]}...")
    print()

    reranked = rerank(query, candidates, top_k=TOP_K)

    print("── After reranking (cross-encoder order) ──")
    for i, c in enumerate(reranked, 1):
        moved = c["original_rank"] - i
        arrow = f"(was #{c['original_rank']})" if moved != 0 else "(unchanged)"
        print(f"  {i}. {c['ticker']:6} [{c.get('section') or '-':20}] "
              f"score={c['rerank_score']:.2f} {arrow}")
        print(f"     {c['text'][:75]}...")
    print()

    # Show how much the ordering actually changed
    before_ids = [c["id"] for c in candidates[:TOP_K]]
    after_ids = [c["id"] for c in reranked]
    changed = sum(1 for a, b in zip(before_ids, after_ids) if a != b)
    print(f"Reranking changed {changed} of the top {TOP_K} positions")


if __name__ == "__main__":
    main()
