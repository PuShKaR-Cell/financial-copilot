"""Step 16a — Export chunks from Qdrant for GPU classification.

Pulls every text chunk's ID and preview text out of Qdrant and
writes them to a CSV that can be uploaded to Google Colab.

This is the "export" half of the export -> compute -> import
pattern used whenever a job is too heavy for local hardware.
Only chunks that haven't been classified yet are exported, so
this is safe to re-run.
"""

import os
import sys
import csv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import settings

from qdrant_client import QdrantClient

COLLECTION_NAME = "text_chunks"
OUTPUT_CSV = os.path.join("data", "processed", "chunks_for_classification.csv")


def main():
    client = QdrantClient(url=settings.qdrant_url)

    print(f"Scanning {COLLECTION_NAME}...")

    rows = []
    offset = None
    total_seen = 0

    while True:
        points, offset = client.scroll(
            collection_name=COLLECTION_NAME,
            limit=1000,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )

        for p in points:
            total_seen += 1
            # Skip chunks that already have a section label
            if "section" in p.payload:
                continue
            preview = (p.payload.get("preview") or "").replace("\n", " ").strip()
            if not preview:
                continue
            rows.append((p.id, preview))

        if offset is None:
            break

    os.makedirs(os.path.dirname(OUTPUT_CSV), exist_ok=True)

    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["point_id", "text"])
        writer.writerows(rows)

    size_mb = os.path.getsize(OUTPUT_CSV) / (1024 * 1024)

    print(f"Total chunks in collection: {total_seen}")
    print(f"Unclassified chunks exported: {len(rows)}")
    print(f"Written to: {OUTPUT_CSV}  ({size_mb:.1f} MB)")
    print()
    print("Next: upload this CSV to Google Colab")


if __name__ == "__main__":
    main()