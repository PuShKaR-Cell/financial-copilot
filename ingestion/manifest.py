"""Step 10 — Ingestion manifest.

A simple tracking layer that records what has been ingested,
when, and a hash of the source content. Any ingestion script
can use this to skip already-processed items on re-runs.

Usage:
    from ingestion.manifest import is_ingested, mark_ingested

    if not is_ingested("edgar_filing", "MSFT_10-Q_2025-06-30"):
        # ... do the actual ingestion work ...
        mark_ingested("edgar_filing", "MSFT_10-Q_2025-06-30", source_hash="abc123")

The source_hash is optional — it lets you detect when a source
has been *updated* (same ID but different content), not just
whether it's been seen before.
"""

import os
import sys
import hashlib
import psycopg2

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import settings


def get_connection():
    """Get a Postgres connection."""
    return psycopg2.connect(settings.postgres_url)


def is_ingested(source, source_id, source_hash=None):
    """Check if a source item has already been ingested.

    Args:
        source: category string, e.g. "edgar_filing", "xbrl", "audio"
        source_id: unique ID within that category, e.g. "MSFT_10-Q_2025-06-30"
        source_hash: if provided, also checks if the content has changed
                     (returns False if the hash doesn't match, meaning
                     the source was updated and needs re-processing)

    Returns:
        True if already ingested (and hash matches, if provided)
        False if not yet ingested or content has changed
    """
    conn = get_connection()
    cur = conn.cursor()

    try:
        cur.execute(
            "SELECT source_hash FROM ingestion_manifest WHERE source = %s AND source_id = %s",
            (source, source_id),
        )
        row = cur.fetchone()

        if row is None:
            return False

        # If a hash was provided, check if content changed
        if source_hash is not None and row[0] != source_hash:
            return False

        return True

    finally:
        cur.close()
        conn.close()


def mark_ingested(source, source_id, source_hash=None):
    """Record that a source item has been ingested.

    If the item was already recorded, updates the timestamp and hash.

    Args:
        source: category string
        source_id: unique ID within that category
        source_hash: optional content hash for change detection
    """
    conn = get_connection()
    cur = conn.cursor()

    try:
        cur.execute(
            """
            INSERT INTO ingestion_manifest (source, source_id, source_hash)
            VALUES (%s, %s, %s)
            ON CONFLICT (source, source_id)
            DO UPDATE SET source_hash = EXCLUDED.source_hash,
                          ingested_at = NOW()
            """,
            (source, source_id, source_hash),
        )
        conn.commit()

    finally:
        cur.close()
        conn.close()


def get_manifest_stats():
    """Get a summary of what's been ingested, grouped by source type."""
    conn = get_connection()
    cur = conn.cursor()

    try:
        cur.execute(
            """
            SELECT source, COUNT(*), MAX(ingested_at)
            FROM ingestion_manifest
            GROUP BY source
            ORDER BY source
            """
        )
        rows = cur.fetchall()
        return [(r[0], r[1], r[2]) for r in rows]

    finally:
        cur.close()
        conn.close()


def hash_file(filepath):
    """Compute a SHA-256 hash of a file's contents.

    Useful for detecting when a filing has been amended/restated
    (same accession but different content).
    """
    sha = hashlib.sha256()
    with open(filepath, "rb") as f:
        while True:
            chunk = f.read(8192)
            if not chunk:
                break
            sha.update(chunk)
    return sha.hexdigest()


# ── CLI: backfill existing data + show stats ───────────────

def backfill():
    """Scan existing downloaded files and register them in the manifest.

    Run this once to catch up the manifest with data that was
    downloaded before the manifest existed (Steps 6-9).
    """
    count = 0

    # Backfill EDGAR filings (Step 6)
    filings_dir = os.path.join("data", "raw", "filings")
    if os.path.exists(filings_dir):
        for ticker in os.listdir(filings_dir):
            ticker_dir = os.path.join(filings_dir, ticker)
            if not os.path.isdir(ticker_dir):
                continue
            for filename in os.listdir(ticker_dir):
                filepath = os.path.join(ticker_dir, filename)
                source_id = f"{ticker}/{filename}"
                if not is_ingested("edgar_filing", source_id):
                    file_hash = hash_file(filepath)
                    mark_ingested("edgar_filing", source_id, file_hash)
                    count += 1

    # Backfill transcripts (Step 9)
    transcripts_dir = os.path.join("data", "raw", "transcripts")
    if os.path.exists(transcripts_dir):
        for filename in os.listdir(transcripts_dir):
            if not filename.endswith(".pdf"):
                continue
            filepath = os.path.join(transcripts_dir, filename)
            if not is_ingested("fomc_transcript", filename):
                file_hash = hash_file(filepath)
                mark_ingested("fomc_transcript", filename, file_hash)
                count += 1

    # Backfill audio (Step 9)
    audio_dir = os.path.join("data", "raw", "audio")
    if os.path.exists(audio_dir):
        for filename in os.listdir(audio_dir):
            if not filename.endswith(".mp3"):
                continue
            filepath = os.path.join(audio_dir, filename)
            if not is_ingested("fomc_audio", filename):
                file_hash = hash_file(filepath)
                mark_ingested("fomc_audio", filename, file_hash)
                count += 1

    return count


def main():
    print("Backfilling manifest from existing data...")
    count = backfill()
    print(f"Registered {count} new items")
    print()

    stats = get_manifest_stats()
    if stats:
        print("=== Manifest Summary ===")
        for source, total, last_at in stats:
            print(f"  {source}: {total} items (last: {last_at.strftime('%Y-%m-%d %H:%M')})")
    else:
        print("Manifest is empty — no data ingested yet")


if __name__ == "__main__":
    main()