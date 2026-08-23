"""Step 16b — Import GPU-computed section labels back into Qdrant.

The "import" half of the export -> compute -> import pattern.
Reads the CSV produced on Colab and writes each section label
onto its corresponding point in the text_chunks collection.

Uses batched payload updates grouped by label, which is far
faster than one API call per point.
"""

import os
import sys
import csv
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import settings

from qdrant_client import QdrantClient

COLLECTION_NAME = "text_chunks"
INPUT_CSV = os.path.join("data", "processed", "classified_sections.csv")
BATCH_SIZE = 500


def main():
    if not os.path.exists(INPUT_CSV):
        print(f"ERROR: {INPUT_CSV} not found")
        print("Download classified_sections.csv from Colab into data/processed/")
        sys.exit(1)

    client = QdrantClient(url=settings.qdrant_url)

    # Read the CSV and group point IDs by section label.
    # Grouping lets us set the same payload on many points at once.
    by_section = defaultdict(list)
    confidences = {}
    total = 0

    with open(INPUT_CSV, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            point_id = int(row["point_id"])
            section = row["section"]
            conf = float(row["confidence"])
            by_section[section].append(point_id)
            confidences[point_id] = conf
            total += 1

    print(f"Read {total} labels from {INPUT_CSV}")
    print(f"Sections found: {len(by_section)}")
    print()

    # Write section labels in batches, one group per section
    updated = 0
    for section, point_ids in sorted(by_section.items(), key=lambda x: -len(x[1])):
        print(f"── {section}: {len(point_ids)} chunks ──")

        for i in range(0, len(point_ids), BATCH_SIZE):
            batch = point_ids[i:i + BATCH_SIZE]
            client.set_payload(
                collection_name=COLLECTION_NAME,
                payload={"section": section},
                points=batch,
            )
            updated += len(batch)

        print(f"  updated")

    print()
    print(f"Done! {updated} chunks tagged with section labels")


if __name__ == "__main__":
    main()