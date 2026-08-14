"""Step 15 — Chunk and embed prose text as a fallback retriever.

Extracts raw text from HTML filings, splits it into overlapping
chunks (~400 tokens each), embeds them, and uploads to Qdrant's
text_chunks collection.

Why this exists alongside visual retrieval (Step 12):
  Visual embeddings are strong on layout-heavy pages (tables, charts)
  but text embeddings are better at matching specific phrases and
  concepts in dense prose (risk factors, legal language, MD&A narrative).
  The Retrieval Agent (Step 29) searches both and merges results —
  this hybrid approach catches things either method alone would miss.
"""

import os
import sys
import re
import hashlib
from bs4 import BeautifulSoup
from sentence_transformers import SentenceTransformer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import settings

from qdrant_client import QdrantClient
from qdrant_client.models import PointStruct

# ── Constants ──────────────────────────────────────────────

FILINGS_DIR = os.path.join("data", "raw", "filings")
TEXT_MODEL = "all-MiniLM-L6-v2"    # 384-dim, matches text_chunks collection
COLLECTION_NAME = "text_chunks"
CHUNK_SIZE = 400       # target tokens per chunk (roughly 1 paragraph)
CHUNK_OVERLAP = 80     # overlap between consecutive chunks
BATCH_SIZE = 64        # chunks per embedding batch


def get_qdrant():
    return QdrantClient(url=settings.qdrant_url)


def extract_text_from_html(html_path):
    """Extract readable text from an HTML filing.

    Strips all tags, scripts, styles, and SEC-specific formatting
    artifacts. Returns one long string of clean text.
    """
    with open(html_path, "r", encoding="utf-8", errors="replace") as f:
        soup = BeautifulSoup(f.read(), "html.parser")

    # Remove script and style elements
    for tag in soup(["script", "style"]):
        tag.decompose()

    # Get text
    text = soup.get_text(separator=" ")

    # Clean up
    text = re.sub(r'\xa0', ' ', text)           # non-breaking spaces
    text = re.sub(r'\s+', ' ', text)            # collapse whitespace
    text = re.sub(r'\n\s*\n', '\n', text)       # collapse blank lines

    return text.strip()


def split_into_chunks(text, chunk_size=CHUNK_SIZE, overlap=CHUNK_OVERLAP):
    """Split text into overlapping chunks by word count.

    Uses word count as a proxy for tokens (~1.3 words per token).
    Overlap ensures that a relevant passage split across two chunks
    is still findable in at least one of them.

    Returns a list of chunk strings.
    """
    words = text.split()
    target_words = int(chunk_size * 1.3)    # rough token-to-word ratio
    overlap_words = int(overlap * 1.3)

    if len(words) <= target_words:
        return [text] if len(words) > 20 else []

    chunks = []
    start = 0

    while start < len(words):
        end = start + target_words
        chunk = " ".join(words[start:end])

        # Only keep chunks with enough content
        if len(chunk) > 50:
            chunks.append(chunk)

        start = end - overlap_words

    return chunks


def make_point_id(ticker, filing, chunk_index):
    """Deterministic ID for deduplication."""
    key = f"text/{ticker}/{filing}/{chunk_index}"
    return int(hashlib.md5(key.encode()).hexdigest()[:16], 16)


def get_existing_ids(client):
    """Get already-embedded chunk IDs from Qdrant."""
    existing = set()
    offset = None
    while True:
        result = client.scroll(
            collection_name=COLLECTION_NAME,
            limit=1000,
            offset=offset,
            with_payload=False,
            with_vectors=False,
        )
        points, offset = result
        for point in points:
            existing.add(point.id)
        if offset is None:
            break
    return existing


def process_filing(html_path, ticker, filing_name, model, client, existing_ids):
    """Extract text, chunk it, embed it, upload to Qdrant.

    Returns (num_chunks, num_uploaded).
    """
    # Extract and chunk
    text = extract_text_from_html(html_path)
    chunks = split_into_chunks(text)

    if not chunks:
        return 0, 0

    # Filter out already-embedded chunks
    new_chunks = []
    for i, chunk in enumerate(chunks):
        point_id = make_point_id(ticker, filing_name, i)
        if point_id not in existing_ids:
            new_chunks.append((i, chunk, point_id))

    if not new_chunks:
        return len(chunks), 0

    # Embed in batches
    uploaded = 0
    for batch_start in range(0, len(new_chunks), BATCH_SIZE):
        batch = new_chunks[batch_start:batch_start + BATCH_SIZE]
        texts = [c[1] for c in batch]
        embeddings = model.encode(texts, show_progress_bar=False)

        points = []
        for (i, chunk, point_id), embedding in zip(batch, embeddings):
            points.append(PointStruct(
                id=point_id,
                vector=embedding.tolist(),
                payload={
                    "ticker": ticker,
                    "filing": filing_name,
                    "chunk_index": i,
                    "total_chunks": len(chunks),
                    "preview": chunk[:200],
                },
            ))

        client.upsert(collection_name=COLLECTION_NAME, points=points)
        uploaded += len(points)

    return len(chunks), uploaded


def main():
    if not os.path.exists(FILINGS_DIR):
        print(f"No filings found at {FILINGS_DIR}")
        print("Run ingestion/edgar.py first (Step 6)")
        sys.exit(1)

    print(f"Model: {TEXT_MODEL}")
    print(f"Chunk size: ~{CHUNK_SIZE} tokens, overlap: {CHUNK_OVERLAP}")
    print()

    # Load model
    print("Loading embedding model...")
    model = SentenceTransformer(TEXT_MODEL)
    print("Model loaded")
    print()

    # Connect to Qdrant
    client = get_qdrant()
    existing_ids = get_existing_ids(client)
    print(f"Already in Qdrant: {len(existing_ids)} text chunks")
    print()

    # Process each filing
    total_chunks = 0
    total_uploaded = 0

    tickers = sorted(os.listdir(FILINGS_DIR))
    for ticker in tickers:
        ticker_dir = os.path.join(FILINGS_DIR, ticker)
        if not os.path.isdir(ticker_dir):
            continue

        html_files = sorted([f for f in os.listdir(ticker_dir) if f.endswith(".html")])
        ticker_chunks = 0
        ticker_uploaded = 0

        for filename in html_files:
            filepath = os.path.join(ticker_dir, filename)
            filing_name = os.path.splitext(filename)[0]

            num_chunks, num_uploaded = process_filing(
                filepath, ticker, filing_name, model, client, existing_ids
            )
            ticker_chunks += num_chunks
            ticker_uploaded += num_uploaded

        total_chunks += ticker_chunks
        total_uploaded += ticker_uploaded
        print(f"  {ticker}: {ticker_chunks} chunks, {ticker_uploaded} new")

    print()
    print(f"Done!")
    print(f"  Total chunks: {total_chunks}")
    print(f"  New embeddings uploaded: {total_uploaded}")
    print(f"  Collection now has: {len(existing_ids) + total_uploaded} chunks")


if __name__ == "__main__":
    main()