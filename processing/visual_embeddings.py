"""Step 12 — Generate visual document embeddings.

Embeds each page image using a vision model so that retrieval
can match queries against what the page *looks like* — tables,
charts, layout, and text together — not just extracted text.

Current model: CLIP (clip-ViT-B-32) via sentence-transformers
  - Runs on CPU at ~10-15 images/second
  - Produces a single 512-dim vector per page image
  - Good general visual understanding

Upgrade path (with GPU): swap to ColPali/ColQwen for multi-vector,
layout-aware embeddings — change MODEL_NAME and the Qdrant
collection config. The rest of the pipeline stays the same.

Output: embeddings are stored in Qdrant's visual_pages collection,
with metadata pointing back to the source filing and page number.
"""

import os
import sys
import glob
import time
from PIL import Image
from sentence_transformers import SentenceTransformer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import settings

try:
    from qdrant_client import QdrantClient
    from qdrant_client.models import (
        VectorParams, Distance, PointStruct
    )
except ImportError:
    print("ERROR: qdrant-client not installed. Run: pip install qdrant-client")
    sys.exit(1)

# ── Constants ──────────────────────────────────────────────

MODEL_NAME = "clip-ViT-B-32"  # swap to ColPali/ColQwen with GPU
VECTOR_DIM = 512              # CLIP-ViT-B-32 output dimension
COLLECTION_NAME = "visual_pages"
IMAGES_DIR = os.path.join("data", "processed", "page_images")
BATCH_SIZE = 32               # images per embedding batch


def get_qdrant():
    """Connect to the local Qdrant instance."""
    return QdrantClient(url=settings.qdrant_url)


def ensure_collection(client):
    """Create the visual_pages collection if it doesn't exist."""
    collections = [c.name for c in client.get_collections().collections]

    if COLLECTION_NAME in collections:
        return False  # already exists

    client.create_collection(
        collection_name=COLLECTION_NAME,
        vectors_config=VectorParams(
            size=VECTOR_DIM,
            distance=Distance.COSINE,
        ),
    )
    return True


def get_existing_ids(client):
    """Get the set of point IDs already in the collection.

    Used to skip pages that have already been embedded,
    making re-runs incremental.
    """
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


def make_point_id(ticker, filing_name, page_num):
    """Create a deterministic ID for a page.

    Uses a hash so the same page always gets the same ID,
    which is what makes re-runs skip already-embedded pages.
    """
    import hashlib
    key = f"{ticker}/{filing_name}/page_{page_num:03d}"
    return int(hashlib.md5(key.encode()).hexdigest()[:16], 16)


def gather_pages():
    """Scan the page_images directory and build a list of all pages.

    Returns a list of dicts with path, ticker, filing, page number.
    """
    pages = []

    if not os.path.exists(IMAGES_DIR):
        return pages

    for ticker in sorted(os.listdir(IMAGES_DIR)):
        ticker_dir = os.path.join(IMAGES_DIR, ticker)
        if not os.path.isdir(ticker_dir):
            continue

        for filing_name in sorted(os.listdir(ticker_dir)):
            filing_dir = os.path.join(ticker_dir, filing_name)
            if not os.path.isdir(filing_dir):
                continue

            image_files = sorted(glob.glob(os.path.join(filing_dir, "page_*.png")))
            for img_path in image_files:
                # Extract page number from filename like page_001.png
                basename = os.path.splitext(os.path.basename(img_path))[0]
                page_num = int(basename.split("_")[1])

                pages.append({
                    "path": img_path,
                    "ticker": ticker,
                    "filing": filing_name,
                    "page_num": page_num,
                    "point_id": make_point_id(ticker, filing_name, page_num),
                })

    return pages


def embed_and_upload(model, client, pages):
    """Embed a list of page images and upload to Qdrant.

    Processes in batches for efficiency.
    """
    uploaded = 0
    errors = 0

    for i in range(0, len(pages), BATCH_SIZE):
        batch = pages[i:i + BATCH_SIZE]

        # Load images
        images = []
        valid_pages = []
        for page in batch:
            try:
                img = Image.open(page["path"]).convert("RGB")
                images.append(img)
                valid_pages.append(page)
            except Exception as e:
                print(f"    Warning: could not open {page['path']}: {e}")
                errors += 1

        if not images:
            continue

        # Embed the batch
        embeddings = model.encode(images, batch_size=len(images), show_progress_bar=False)

        # Build Qdrant points
        points = []
        for page, embedding in zip(valid_pages, embeddings):
            points.append(PointStruct(
                id=page["point_id"],
                vector=embedding.tolist(),
                payload={
                    "ticker": page["ticker"],
                    "filing": page["filing"],
                    "page_num": page["page_num"],
                    "source_path": page["path"],
                },
            ))

        # Upload to Qdrant
        client.upsert(collection_name=COLLECTION_NAME, points=points)
        uploaded += len(points)

        # Close images to free memory
        for img in images:
            img.close()

    return uploaded, errors


def main():
    if not os.path.exists(IMAGES_DIR):
        print(f"No page images found at {IMAGES_DIR}")
        print("Run processing/pdf_to_images.py first (Step 11)")
        sys.exit(1)

    print(f"Model: {MODEL_NAME}")
    print(f"Vector dimensions: {VECTOR_DIM}")
    print(f"Batch size: {BATCH_SIZE}")
    print()

    # Load model (first run downloads it, ~350MB)
    print("Loading embedding model...")
    model = SentenceTransformer(MODEL_NAME)
    print("Model loaded")
    print()

    # Connect to Qdrant
    client = get_qdrant()
    created = ensure_collection(client)
    if created:
        print(f"Created Qdrant collection: {COLLECTION_NAME}")
    else:
        print(f"Using existing collection: {COLLECTION_NAME}")

    # Get already-embedded page IDs
    existing_ids = get_existing_ids(client)
    print(f"Already embedded: {len(existing_ids)} pages")
    print()

    # Gather all pages
    all_pages = gather_pages()
    print(f"Total pages on disk: {len(all_pages)}")

    # Filter out already-embedded pages
    new_pages = [p for p in all_pages if p["point_id"] not in existing_ids]
    print(f"New pages to embed: {len(new_pages)}")

    if not new_pages:
        print("Nothing to do — all pages already embedded")
        return

    # Estimate time
    est_seconds = len(new_pages) / 12  # rough estimate at ~12 img/sec on CPU
    est_minutes = est_seconds / 60
    print(f"Estimated time: ~{est_minutes:.0f} minutes on CPU")
    print()

    # Process by ticker for clearer progress
    tickers = sorted(set(p["ticker"] for p in new_pages))
    total_uploaded = 0
    total_errors = 0
    start_time = time.time()

    for ticker in tickers:
        ticker_pages = [p for p in new_pages if p["ticker"] == ticker]
        print(f"── {ticker}: {len(ticker_pages)} pages ──")

        uploaded, errors = embed_and_upload(model, client, ticker_pages)
        total_uploaded += uploaded
        total_errors += errors

        elapsed = time.time() - start_time
        speed = total_uploaded / elapsed if elapsed > 0 else 0
        remaining = (len(new_pages) - total_uploaded) / speed if speed > 0 else 0
        print(f"  ✓ {uploaded} embedded ({speed:.1f} pages/sec, ~{remaining/60:.0f}min remaining)")
        print()

    elapsed = time.time() - start_time
    print(f"Done! Embedded: {total_uploaded}, Errors: {total_errors}")
    print(f"Total time: {elapsed/60:.1f} minutes")
    print(f"Collection now has {len(existing_ids) + total_uploaded} pages")


if __name__ == "__main__":
    main()