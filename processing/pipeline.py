"""Step 18 — End-to-end document processing pipeline.

Runs the full Phase 2 chain in dependency order:

    13. qdrant_setup       ensure collections exist
    11. pdf_to_images      HTML filings  -> page images
    12. visual_embeddings  page images   -> visual_pages collection
    14. table_extraction   HTML filings  -> Postgres + table_chunks
    15. text_chunking      HTML filings  -> text_chunks
    16. section labels     reported only (needs the GPU round-trip)

Every stage is individually idempotent, so the whole pipeline is too:
running it after a single new filing lands processes only that filing
and leaves the other 118 untouched.

The reranker (Step 17) is deliberately not here — it runs at query
time inside the Retrieval Agent, not at ingest time.

Usage:
    python processing/pipeline.py            # run everything
    python processing/pipeline.py --status   # report state, change nothing
"""

import os
import sys
import time
import argparse
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import settings

from qdrant_client import QdrantClient

# Import each stage's entry point
from processing import qdrant_setup
from processing import pdf_to_images
from processing import visual_embeddings
from processing import table_extraction
from processing import text_chunking


# Stages run in dependency order. Collections must exist before
# anything tries to write into them, and page images must exist
# before they can be embedded.
STAGES = [
    ("13", "Qdrant collections", qdrant_setup.main),
    ("11", "Filing pages -> images", pdf_to_images.main),
    ("12", "Page images -> visual embeddings", visual_embeddings.main),
    ("14", "Table extraction", table_extraction.main),
    ("15", "Text chunking", text_chunking.main),
]


def get_status():
    """Report current pipeline state without changing anything."""
    client = QdrantClient(url=settings.qdrant_url)
    existing = {c.name for c in client.get_collections().collections}

    print("── Collections ──")
    for name in ("visual_pages", "text_chunks", "table_chunks"):
        if name in existing:
            count = client.get_collection(name).points_count
            print(f"  {name:16} {count:>7,} points")
        else:
            print(f"  {name:16} {'missing':>7}")

    # Raw inputs on disk
    filings_dir = os.path.join("data", "raw", "filings")
    images_dir = os.path.join("data", "processed", "page_images")

    n_filings = 0
    if os.path.isdir(filings_dir):
        for ticker in os.listdir(filings_dir):
            d = os.path.join(filings_dir, ticker)
            if os.path.isdir(d):
                n_filings += len([f for f in os.listdir(d) if f.endswith(".html")])

    n_rendered = 0
    if os.path.isdir(images_dir):
        for ticker in os.listdir(images_dir):
            d = os.path.join(images_dir, ticker)
            if os.path.isdir(d):
                n_rendered += len([f for f in os.listdir(d)
                                   if os.path.isdir(os.path.join(d, f))])

    print()
    print("── Documents ──")
    print(f"  filings on disk      {n_filings:>7,}")
    print(f"  filings rendered     {n_rendered:>7,}")

    # Section-label coverage (Step 16)
    if "text_chunks" in existing:
        labelled = 0
        total = 0
        offset = None
        while True:
            points, offset = client.scroll(
                collection_name="text_chunks",
                limit=1000, offset=offset,
                with_payload=True, with_vectors=False,
            )
            for p in points:
                total += 1
                if "section" in p.payload:
                    labelled += 1
            if offset is None:
                break

        pct = (labelled / total * 100) if total else 0
        print()
        print("── Section labels (Step 16) ──")
        print(f"  labelled             {labelled:>7,} / {total:,}  ({pct:.0f}%)")
        if labelled < total:
            print()
            print(f"  {total - labelled:,} chunks still need section labels.")
            print("  These need the GPU round-trip:")
            print("    1. python processing/export_for_colab.py")
            print("    2. run the Colab notebook on the exported CSV")
            print("    3. python processing/import_from_colab.py")


def run_pipeline():
    """Run every stage in order, reporting timing and failures."""
    print("=" * 60)
    print("  Phase 2 — document processing pipeline")
    print("=" * 60)
    print()

    overall_start = time.time()
    results = []

    for step_no, label, fn in STAGES:
        print("─" * 60)
        print(f"  Step {step_no} — {label}")
        print("─" * 60)

        start = time.time()
        try:
            fn()
            elapsed = time.time() - start
            results.append((step_no, label, "ok", elapsed))
            print(f"\n  [Step {step_no} finished in {elapsed:.1f}s]")
        except Exception as e:
            elapsed = time.time() - start
            results.append((step_no, label, f"FAILED: {e}", elapsed))
            print(f"\n  [Step {step_no} FAILED after {elapsed:.1f}s]")
            traceback.print_exc()
            print()
            print("  Stopping — later stages depend on this one.")
            break

        print()

    total = time.time() - overall_start

    print("=" * 60)
    print("  Summary")
    print("=" * 60)
    for step_no, label, status, elapsed in results:
        mark = "OK  " if status == "ok" else "FAIL"
        print(f"  [{mark}] Step {step_no:<3} {label:<38} {elapsed:>7.1f}s")
    print()
    print(f"  Total: {total/60:.1f} minutes")
    print()

    if all(r[2] == "ok" for r in results):
        print("── Current state ──")
        print()
        get_status()


def main():
    parser = argparse.ArgumentParser(description="Phase 2 processing pipeline")
    parser.add_argument("--status", action="store_true",
                        help="report current state without processing anything")
    args = parser.parse_args()

    if args.status:
        get_status()
    else:
        run_pipeline()


if __name__ == "__main__":
    main()