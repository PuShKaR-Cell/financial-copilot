"""Step 13 — Set up Qdrant collections.

Creates all three collections used by the retrieval system:

1. visual_pages  — page-level image embeddings (Step 12, already exists)
2. text_chunks   — prose text chunk embeddings (populated in Step 15)
3. table_chunks  — extracted table embeddings (populated in Step 14)

The retrieval agent (Step 29) searches all three and merges
results — this hybrid approach is more robust than any single
retrieval method alone.

Safe to run multiple times — skips collections that already exist.
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import settings

from qdrant_client import QdrantClient
from qdrant_client.models import VectorParams, Distance


# ── Collection definitions ─────────────────────────────────
# Each collection uses the same embedding model (CLIP or a text
# encoder) so dimensions must match. text_chunks and table_chunks
# use a text embedding model (all-MiniLM-L6-v2, 384 dims) since
# their content is text, not images.

COLLECTIONS = {
    "visual_pages": {
        "size": 512,          # CLIP-ViT-B-32 output
        "distance": Distance.COSINE,
        "description": "Page-level image embeddings from filing PDFs",
    },
    "text_chunks": {
        "size": 384,          # all-MiniLM-L6-v2 output
        "distance": Distance.COSINE,
        "description": "Prose text chunks from filing sections (MD&A, Risk Factors, etc.)",
    },
    "table_chunks": {
        "size": 384,          # all-MiniLM-L6-v2 output
        "distance": Distance.COSINE,
        "description": "Extracted financial tables serialized as text",
    },
}


def main():
    client = QdrantClient(url=settings.qdrant_url)
    existing = [c.name for c in client.get_collections().collections]

    print("Setting up Qdrant collections...")
    print()

    for name, config in COLLECTIONS.items():
        if name in existing:
            # Get current count
            info = client.get_collection(name)
            count = info.points_count
            print(f"  ✓ {name} — already exists ({count} points)")
        else:
            client.create_collection(
                collection_name=name,
                vectors_config=VectorParams(
                    size=config["size"],
                    distance=config["distance"],
                ),
            )
            print(f"  + {name} — created ({config['size']}-dim, {config['distance']})")

        print(f"    {config['description']}")
        print()

    print("All collections ready")


if __name__ == "__main__":
    main()