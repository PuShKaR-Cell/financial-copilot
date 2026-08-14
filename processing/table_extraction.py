"""Step 14 — Extract structured tables from SEC filings.

Parses HTML filing documents to pull out financial tables
(income statements, balance sheets, segment breakdowns, etc.)
and stores them in two places:

1. Postgres (table_data table) — structured rows/columns for
   the Table QA agent to query directly (Step 24)
2. Qdrant (table_chunks collection) — text-serialized tables
   for semantic search by the Retrieval Agent (Step 29)

SEC filings use <table> tags heavily for layout/formatting,
not just data. The script filters for tables that actually
contain financial data by checking for numeric content and
minimum size thresholds.
"""

import os
import sys
import re
import json
import hashlib
from bs4 import BeautifulSoup
from sentence_transformers import SentenceTransformer
import psycopg2

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import settings

from qdrant_client import QdrantClient
from qdrant_client.models import PointStruct

# ── Constants ──────────────────────────────────────────────

FILINGS_DIR = os.path.join("data", "raw", "filings")
TEXT_MODEL = "all-MiniLM-L6-v2"   # matches text_chunks/table_chunks dim (384)
COLLECTION_NAME = "table_chunks"
MIN_ROWS = 3          # ignore tables with fewer rows than this
MIN_NUMERIC_CELLS = 3  # must have at least this many cells with numbers


def get_qdrant():
    return QdrantClient(url=settings.qdrant_url)


def get_db():
    return psycopg2.connect(settings.postgres_url)


def ensure_table_data_table():
    """Create the table_data Postgres table if it doesn't exist."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS table_data (
            id SERIAL PRIMARY KEY,
            ticker VARCHAR(10) NOT NULL,
            filing VARCHAR(200) NOT NULL,
            table_index INTEGER NOT NULL,
            table_text TEXT,
            headers TEXT,
            rows_json TEXT,
            num_rows INTEGER,
            num_cols INTEGER,
            UNIQUE(ticker, filing, table_index)
        );
    """)
    conn.commit()
    cur.close()
    conn.close()


def clean_text(text):
    """Clean up text extracted from table cells."""
    if text is None:
        return ""
    text = re.sub(r'\s+', ' ', text).strip()
    # Remove common SEC formatting artifacts
    text = text.replace('\xa0', ' ')  # non-breaking space
    text = text.replace('$', '$ ').replace('  ', ' ')
    return text.strip()


def count_numeric_cells(rows):
    """Count how many cells contain numeric values."""
    count = 0
    for row in rows:
        for cell in row:
            # Check if cell has a number (including negatives, decimals, percentages)
            if re.search(r'-?\d[\d,]*\.?\d*%?', cell):
                count += 1
    return count


def extract_tables_from_html(html_path):
    """Parse an HTML filing and extract meaningful financial tables.

    Returns a list of dicts, each with:
      - headers: list of column header strings
      - rows: list of lists (each row's cell values)
      - text: a plain-text serialization of the table
    """
    with open(html_path, "r", encoding="utf-8", errors="replace") as f:
        soup = BeautifulSoup(f.read(), "html.parser")

    tables = []

    for table_tag in soup.find_all("table"):
        rows = []

        for tr in table_tag.find_all("tr"):
            cells = []
            for td in tr.find_all(["td", "th"]):
                cells.append(clean_text(td.get_text()))
            if cells and any(c for c in cells):  # skip empty rows
                rows.append(cells)

        # Filter out junk tables
        if len(rows) < MIN_ROWS:
            continue

        if count_numeric_cells(rows) < MIN_NUMERIC_CELLS:
            continue

        # Separate headers from data
        # Heuristic: first row is headers if it has mostly non-numeric cells
        headers = rows[0]
        data_rows = rows[1:]

        # Normalize column count (pad short rows with empty strings)
        max_cols = max(len(r) for r in rows) if rows else 0
        headers = headers + [""] * (max_cols - len(headers))
        data_rows = [r + [""] * (max_cols - len(r)) for r in data_rows]

        # Serialize to readable text for embedding
        text_lines = []
        text_lines.append(" | ".join(headers))
        text_lines.append("-" * 40)
        for row in data_rows:
            text_lines.append(" | ".join(row))
        table_text = "\n".join(text_lines)

        # Skip if the text version is too short (likely noise)
        if len(table_text) < 50:
            continue

        tables.append({
            "headers": headers,
            "rows": data_rows,
            "text": table_text,
            "num_rows": len(data_rows),
            "num_cols": max_cols,
        })

    return tables


def make_point_id(ticker, filing, table_index):
    """Deterministic ID for deduplication."""
    key = f"table/{ticker}/{filing}/{table_index}"
    return int(hashlib.md5(key.encode()).hexdigest()[:16], 16)


def store_tables_postgres(ticker, filing_name, tables):
    """Store extracted tables in Postgres."""
    conn = get_db()
    cur = conn.cursor()

    inserted = 0
    for i, table in enumerate(tables):
        try:
            cur.execute("""
                INSERT INTO table_data
                    (ticker, filing, table_index, table_text, headers, rows_json, num_rows, num_cols)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (ticker, filing, table_index) DO NOTHING
            """, (
                ticker,
                filing_name,
                i,
                table["text"],
                json.dumps(table["headers"]),
                json.dumps(table["rows"]),
                table["num_rows"],
                table["num_cols"],
            ))
            if cur.rowcount > 0:
                inserted += 1
        except Exception as e:
            conn.rollback()
            print(f"    Warning: {e}")
            continue

    conn.commit()
    cur.close()
    conn.close()
    return inserted


def embed_and_upload_tables(model, client, ticker, filing_name, tables, existing_ids):
    """Embed table text and upload to Qdrant."""
    new_tables = []
    for i, table in enumerate(tables):
        point_id = make_point_id(ticker, filing_name, i)
        if point_id not in existing_ids:
            new_tables.append((i, table, point_id))

    if not new_tables:
        return 0

    # Embed all new tables at once
    texts = [t[1]["text"][:2000] for t in new_tables]  # truncate very long tables
    embeddings = model.encode(texts, show_progress_bar=False)

    points = []
    for (i, table, point_id), embedding in zip(new_tables, embeddings):
        points.append(PointStruct(
            id=point_id,
            vector=embedding.tolist(),
            payload={
                "ticker": ticker,
                "filing": filing_name,
                "table_index": i,
                "num_rows": table["num_rows"],
                "num_cols": table["num_cols"],
                "preview": table["text"][:300],
            },
        ))

    client.upsert(collection_name=COLLECTION_NAME, points=points)
    return len(points)


def get_existing_ids(client):
    """Get already-embedded table IDs from Qdrant."""
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


def main():
    if not os.path.exists(FILINGS_DIR):
        print(f"No filings found at {FILINGS_DIR}")
        print("Run ingestion/edgar.py first (Step 6)")
        sys.exit(1)

    # Create Postgres table
    ensure_table_data_table()

    # Load text embedding model
    print(f"Loading embedding model ({TEXT_MODEL})...")
    model = SentenceTransformer(TEXT_MODEL)
    print("Model loaded")
    print()

    # Connect to Qdrant
    client = get_qdrant()
    existing_ids = get_existing_ids(client)
    print(f"Already in Qdrant: {len(existing_ids)} table chunks")
    print()

    # Process each filing
    total_tables = 0
    total_pg_inserted = 0
    total_qd_uploaded = 0

    tickers = sorted(os.listdir(FILINGS_DIR))
    for ticker in tickers:
        ticker_dir = os.path.join(FILINGS_DIR, ticker)
        if not os.path.isdir(ticker_dir):
            continue

        html_files = sorted([f for f in os.listdir(ticker_dir) if f.endswith(".html")])
        ticker_tables = 0

        for filename in html_files:
            filepath = os.path.join(ticker_dir, filename)
            filing_name = os.path.splitext(filename)[0]

            tables = extract_tables_from_html(filepath)
            ticker_tables += len(tables)

            if tables:
                pg_inserted = store_tables_postgres(ticker, filing_name, tables)
                total_pg_inserted += pg_inserted

                qd_uploaded = embed_and_upload_tables(
                    model, client, ticker, filing_name, tables, existing_ids
                )
                total_qd_uploaded += qd_uploaded

        total_tables += ticker_tables
        print(f"  {ticker}: {ticker_tables} tables from {len(html_files)} filings")

    print()
    print(f"Done!")
    print(f"  Total tables found: {total_tables}")
    print(f"  Postgres — new rows: {total_pg_inserted}")
    print(f"  Qdrant   — new embeddings: {total_qd_uploaded}")


if __name__ == "__main__":
    main()