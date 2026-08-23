"""Step 22 — Index transcript segments into Qdrant.

Embeds every transcript segment so the Sentiment Agent (Step 31)
can retrieve them semantically, rather than only by exact SQL filter.

Each point carries the metadata computed in Steps 19-21:
  speaker / speaker_normalized / role  — who said it
  sentiment + sentiment_score          — FinBERT tone
  hedging_density                      — uncertainty markers per 100 words
  start_sec / end_sec                  — timestamp, for ASR segments
  source_type                          — 'official' or 'asr'

That means the agent can answer "what did the Chair say about
inflation" AND immediately report the tone of the answer, without
a second lookup.

Uses the same 384-dim text model as Steps 15 and 17, so query
embeddings are directly comparable across collections.
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import settings

import psycopg2
from sentence_transformers import SentenceTransformer
from qdrant_client import QdrantClient
from qdrant_client.models import VectorParams, Distance, PointStruct

# ── Constants ──────────────────────────────────────────────

COLLECTION_NAME = "call_transcripts"
TEXT_MODEL = "all-MiniLM-L6-v2"   # 384-dim, matches text_chunks
VECTOR_DIM = 384
BATCH_SIZE = 64
MAX_CHARS = 2000                  # truncate very long segments


def get_db():
    return psycopg2.connect(settings.postgres_url)


def get_qdrant():
    return QdrantClient(url=settings.qdrant_url)


def ensure_collection(client):
    """Create the call_transcripts collection if it doesn't exist."""
    existing = {c.name for c in client.get_collections().collections}
    if COLLECTION_NAME in existing:
        return False
    client.create_collection(
        collection_name=COLLECTION_NAME,
        vectors_config=VectorParams(size=VECTOR_DIM, distance=Distance.COSINE),
    )
    return True


def get_existing_ids(client):
    """IDs already in the collection, so re-runs are incremental."""
    existing = set()
    offset = None
    while True:
        points, offset = client.scroll(
            collection_name=COLLECTION_NAME,
            limit=1000, offset=offset,
            with_payload=False, with_vectors=False,
        )
        for p in points:
            existing.add(p.id)
        if offset is None:
            break
    return existing


def fetch_segments():
    """Pull all transcript segments with their computed metadata.

    The Postgres primary key doubles as the Qdrant point ID, which
    keeps the two stores trivially joinable and makes dedup exact.
    """
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, event_id, source_type, segment_index,
               speaker, speaker_normalized, role, text,
               start_sec, end_sec,
               sentiment, sentiment_score, hedging_density
        FROM transcript_segments
        ORDER BY id
    """)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


def build_payload(row):
    """Turn a database row into Qdrant payload metadata."""
    (seg_id, event_id, source_type, seg_idx,
     speaker, speaker_norm, role, text,
     start_sec, end_sec,
     sentiment, sentiment_score, hedging) = row

    payload = {
        "event_id": event_id,
        "source_type": source_type,
        "segment_index": seg_idx,
        "preview": text[:300],
    }

    # Only include fields that actually have values — keeps payloads
    # clean and avoids nulls cluttering filter conditions
    if speaker:
        payload["speaker"] = speaker
    if speaker_norm:
        payload["speaker_normalized"] = speaker_norm
    if role:
        payload["role"] = role
    if sentiment:
        payload["sentiment"] = sentiment
    if sentiment_score is not None:
        payload["sentiment_score"] = float(sentiment_score)
    if hedging is not None:
        payload["hedging_density"] = float(hedging)
    if start_sec is not None:
        payload["start_sec"] = float(start_sec)
        # Human-readable timestamp for citations ("at 14:22")
        total = int(float(start_sec))
        payload["timestamp"] = f"{total // 60}:{total % 60:02d}"
    if end_sec is not None:
        payload["end_sec"] = float(end_sec)

    return payload


def report(client):
    """Summarise what's in the collection."""
    info = client.get_collection(COLLECTION_NAME)
    print(f"── {COLLECTION_NAME}: {info.points_count:,} points ──")
    print()

    # Sample search to prove semantic retrieval works
    model = SentenceTransformer(TEXT_MODEL)
    query = "inflation expectations remain anchored"
    print(f"Sample query: {query!r}")
    print()

    vec = model.encode(query).tolist()
    response = client.query_points(
        collection_name=COLLECTION_NAME,
        query=vec,
        limit=3,
        with_payload=True,
    )

    for i, hit in enumerate(response.points, 1):
        p = hit.payload
        who = p.get("speaker_normalized") or p.get("speaker") or "unknown"
        tone = p.get("sentiment", "-")
        hedge = p.get("hedging_density", "-")
        print(f"  {i}. [{p.get('event_id')}] {who}  "
              f"(tone={tone}, hedging={hedge}, score={hit.score:.3f})")
        print(f"     {p.get('preview', '')[:110]}...")
        print()


def main():
    client = get_qdrant()

    created = ensure_collection(client)
    print(f"{'Created' if created else 'Using existing'} collection: {COLLECTION_NAME}")

    existing_ids = get_existing_ids(client)
    print(f"Already indexed: {len(existing_ids)} segments")

    rows = fetch_segments()
    print(f"Segments in Postgres: {len(rows)}")

    new_rows = [r for r in rows if r[0] not in existing_ids]
    print(f"New segments to index: {len(new_rows)}")
    print()

    if not new_rows:
        print("Nothing to index — all segments already in Qdrant")
        print()
        report(client)
        return

    print(f"Loading {TEXT_MODEL}...")
    model = SentenceTransformer(TEXT_MODEL)
    print("Model loaded")
    print()

    start = time.time()
    uploaded = 0

    for i in range(0, len(new_rows), BATCH_SIZE):
        batch = new_rows[i:i + BATCH_SIZE]
        texts = [r[7][:MAX_CHARS] for r in batch]   # index 7 = text

        embeddings = model.encode(texts, show_progress_bar=False)

        points = [
            PointStruct(
                id=row[0],                       # Postgres id = Qdrant id
                vector=emb.tolist(),
                payload=build_payload(row),
            )
            for row, emb in zip(batch, embeddings)
        ]

        client.upsert(collection_name=COLLECTION_NAME, points=points)
        uploaded += len(points)

        if uploaded % (BATCH_SIZE * 4) == 0 or uploaded == len(new_rows):
            print(f"  {uploaded}/{len(new_rows)} indexed")

    print()
    print(f"Done! Indexed {uploaded} segments in {time.time() - start:.1f}s")
    print()

    report(client)


if __name__ == "__main__":
    main()