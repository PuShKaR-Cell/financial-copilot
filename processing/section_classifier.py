"""Step 16 — Zero-shot classification of filing sections.

Tags each text chunk in Qdrant with its most likely filing section
(MD&A, Risk Factors, Financial Statements, etc.) using a zero-shot
classification model.

Why this matters:
  When the Retrieval Agent gets "what are the supply chain risks,"
  it can filter to just Risk Factors chunks before searching,
  instead of scanning all 14,000+ chunks. This meaningfully
  improves both precision and speed.

The labels are stored as metadata on existing Qdrant points —
no new collections needed, just an extra field on each chunk.
"""

import os
import sys
import time
from transformers import pipeline

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import settings

from qdrant_client import QdrantClient
from qdrant_client.models import SetPayloadOperation, SetPayload, PointIdsList

# ── Constants ──────────────────────────────────────────────

COLLECTION_NAME = "text_chunks"
BATCH_SIZE = 16  # chunks per classification batch (smaller = less RAM)

# SEC filing section labels — these are the standard sections
# that appear in every 10-K and 10-Q
SECTION_LABELS = [
    "Management Discussion and Analysis",
    "Risk Factors",
    "Financial Statements and Notes",
    "Business Overview",
    "Legal Proceedings",
    "Executive Compensation",
    "Market Risk Disclosures",
    "Controls and Procedures",
    "Other",
]

# Short labels for cleaner metadata
LABEL_MAP = {
    "Management Discussion and Analysis": "mda",
    "Risk Factors": "risk_factors",
    "Financial Statements and Notes": "financial_statements",
    "Business Overview": "business_overview",
    "Legal Proceedings": "legal",
    "Executive Compensation": "compensation",
    "Market Risk Disclosures": "market_risk",
    "Controls and Procedures": "controls",
    "Other": "other",
}


def get_qdrant():
    return QdrantClient(url=settings.qdrant_url)


def get_unclassified_chunks(client, batch_size=1000):
    """Get chunks that haven't been classified yet.

    Checks for the absence of a 'section' field in the payload.
    Returns list of (point_id, preview_text) tuples.
    """
    unclassified = []
    offset = None

    while True:
        result = client.scroll(
            collection_name=COLLECTION_NAME,
            limit=batch_size,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        points, offset = result

        for point in points:
            if "section" not in point.payload:
                preview = point.payload.get("preview", "")
                unclassified.append((point.id, preview))

        if offset is None:
            break

    return unclassified


def classify_chunks(classifier, chunks):
    """Run zero-shot classification on a batch of chunks.

    Returns a list of (point_id, section_label, confidence) tuples.
    """
    ids = [c[0] for c in chunks]
    texts = [c[1] for c in chunks]

    results = classifier(
        texts,
        candidate_labels=SECTION_LABELS,
        batch_size=BATCH_SIZE,
    )

    # classifier returns a single dict if given one text,
    # or a list of dicts if given multiple
    if isinstance(results, dict):
        results = [results]

    classified = []
    for point_id, result in zip(ids, results):
        top_label = result["labels"][0]
        confidence = result["scores"][0]
        short_label = LABEL_MAP.get(top_label, "other")
        classified.append((point_id, short_label, confidence))

    return classified


def update_qdrant_metadata(client, classified):
    """Write the section label back to each chunk's payload in Qdrant."""
    for point_id, section, confidence in classified:
        client.set_payload(
            collection_name=COLLECTION_NAME,
            payload={
                "section": section,
                "section_confidence": round(confidence, 3),
            },
            points=[point_id],
        )


def main():
    print("Loading zero-shot classifier...")
    print("(First run downloads the model — ~1.1GB)")
    print()

    classifier = pipeline(
        "zero-shot-classification",
        model="facebook/bart-large-mnli",
        device=-1,  # CPU
    )
    print("Model loaded")
    print()

    client = get_qdrant()

    # Find unclassified chunks
    print("Scanning for unclassified chunks...")
    unclassified = get_unclassified_chunks(client)
    print(f"Found {len(unclassified)} unclassified chunks")

    if not unclassified:
        print("Nothing to do — all chunks already classified")
        return

    # Estimate time (~2-3 chunks/sec on CPU with this model)
    est_minutes = len(unclassified) / 2.5 / 60
    print(f"Estimated time: ~{est_minutes:.0f} minutes on CPU")
    print()

    # Process in batches
    total_classified = 0
    start_time = time.time()
    section_counts = {}

    for i in range(0, len(unclassified), BATCH_SIZE):
        batch = unclassified[i:i + BATCH_SIZE]

        classified = classify_chunks(classifier, batch)
        update_qdrant_metadata(client, classified)

        total_classified += len(classified)

        # Track section distribution
        for _, section, _ in classified:
            section_counts[section] = section_counts.get(section, 0) + 1

        # Progress update every 5 batches
        if (i // BATCH_SIZE) % 5 == 0:
            elapsed = time.time() - start_time
            speed = total_classified / elapsed if elapsed > 0 else 0
            remaining = (len(unclassified) - total_classified) / speed if speed > 0 else 0
            print(f"  {total_classified}/{len(unclassified)} classified "
                  f"({speed:.1f}/sec, ~{remaining/60:.0f}min remaining)")

    elapsed = time.time() - start_time
    print()
    print(f"Done! Classified {total_classified} chunks in {elapsed/60:.1f} minutes")
    print()
    print("Section distribution:")
    for section, count in sorted(section_counts.items(), key=lambda x: -x[1]):
        pct = count / total_classified * 100
        print(f"  {section}: {count} ({pct:.1f}%)")


if __name__ == "__main__":
    main()