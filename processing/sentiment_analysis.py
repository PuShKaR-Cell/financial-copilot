"""Step 21 — Sentiment and tone classification on transcript segments.

Scores every transcript segment for financial sentiment using FinBERT,
a BERT model fine-tuned on financial text.

Why FinBERT rather than general sentiment: financial language encodes
sentiment differently from ordinary prose. "Headwinds", "moderating
demand", and "we remain data-dependent" read as neutral to a general
model but carry real directional signal in a financial context.
FinBERT was trained to pick that up.

Alongside the model score, the script computes a hedging density
metric — the rate of uncertainty markers ("could", "may", "we'll see")
per 100 words. Tone and hedging are different signals: a speaker can
be positive but heavily hedged, which is itself informative.

Results are written back onto each row in transcript_segments.
"""

import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import settings

import psycopg2
from transformers import pipeline

MODEL_NAME = "ProsusAI/finbert"
BATCH_SIZE = 16
MAX_CHARS = 1500   # FinBERT truncates at 512 tokens; keep inputs sane


# Uncertainty markers common in central-bank and earnings language.
# Counted as a rate per 100 words so long and short segments compare.
HEDGE_TERMS = [
    r"\bmay\b", r"\bmight\b", r"\bcould\b", r"\bwould\b",
    r"\bperhaps\b", r"\bpossibly\b", r"\bpotentially\b",
    r"\buncertain\w*\b", r"\bdepend\w*\b", r"\bif\b",
    r"\bwe'll see\b", r"\bwait and see\b", r"\bdata.dependent\b",
    r"\bnot\s+(?:sure|certain|clear)\b", r"\bhard to say\b",
    r"\bsome\s+(?:risk|chance)\b", r"\bto some extent\b",
]
HEDGE_RE = re.compile("|".join(HEDGE_TERMS), re.IGNORECASE)


def get_db():
    return psycopg2.connect(settings.postgres_url)


def hedging_density(text):
    """Uncertainty markers per 100 words."""
    words = len(text.split())
    if words < 5:
        return 0.0
    hits = len(HEDGE_RE.findall(text))
    return round(hits / words * 100, 2)


def fetch_unscored():
    """Get segments that don't have a sentiment label yet."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, text
        FROM transcript_segments
        WHERE sentiment IS NULL
        ORDER BY id
    """)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


def store_scores(scored):
    """Write sentiment label + score back to each segment."""
    conn = get_db()
    cur = conn.cursor()
    for seg_id, label, score in scored:
        cur.execute("""
            UPDATE transcript_segments
            SET sentiment = %s, sentiment_score = %s
            WHERE id = %s
        """, (label, score, seg_id))
    conn.commit()
    cur.close()
    conn.close()


def ensure_hedging_column():
    """Add the hedging_density column if it isn't there yet."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        ALTER TABLE transcript_segments
        ADD COLUMN IF NOT EXISTS hedging_density NUMERIC
    """)
    conn.commit()
    cur.close()
    conn.close()


def store_hedging(values):
    conn = get_db()
    cur = conn.cursor()
    for seg_id, density in values:
        cur.execute(
            "UPDATE transcript_segments SET hedging_density = %s WHERE id = %s",
            (density, seg_id),
        )
    conn.commit()
    cur.close()
    conn.close()


def report():
    """Summarise what's in the table after scoring."""
    conn = get_db()
    cur = conn.cursor()

    print("── Sentiment distribution ──")
    cur.execute("""
        SELECT sentiment, COUNT(*), ROUND(AVG(sentiment_score), 3)
        FROM transcript_segments
        WHERE sentiment IS NOT NULL
        GROUP BY sentiment
        ORDER BY COUNT(*) DESC
    """)
    for label, count, avg in cur.fetchall():
        print(f"  {label:10} {count:>5}  (avg confidence {avg})")

    print()
    print("── Most hedged speakers (min 5 segments) ──")
    cur.execute("""
        SELECT speaker, COUNT(*), ROUND(AVG(hedging_density), 2)
        FROM transcript_segments
        WHERE speaker IS NOT NULL AND hedging_density IS NOT NULL
        GROUP BY speaker
        HAVING COUNT(*) >= 5
        ORDER BY AVG(hedging_density) DESC
        LIMIT 8
    """)
    for speaker, count, density in cur.fetchall():
        print(f"  {speaker:24} {density:>5} per 100 words  ({count} segments)")

    print()
    print("── Tone by event (official transcripts) ──")
    cur.execute("""
        SELECT event_id,
               COUNT(*) FILTER (WHERE sentiment = 'positive') AS pos,
               COUNT(*) FILTER (WHERE sentiment = 'negative') AS neg,
               ROUND(AVG(hedging_density), 2) AS hedge
        FROM transcript_segments
        WHERE source_type = 'official' AND sentiment IS NOT NULL
        GROUP BY event_id
        ORDER BY event_id
    """)
    print(f"  {'event':10} {'pos':>4} {'neg':>4} {'hedging':>9}")
    for event, pos, neg, hedge in cur.fetchall():
        print(f"  {event:10} {pos:>4} {neg:>4} {hedge:>9}")

    cur.close()
    conn.close()


def main():
    ensure_hedging_column()

    rows = fetch_unscored()
    print(f"Segments needing sentiment: {len(rows)}")

    if not rows:
        print("Nothing to score — all segments already have sentiment")
        print()
        report()
        return

    print(f"Loading {MODEL_NAME}...")
    clf = pipeline(
        "sentiment-analysis",
        model=MODEL_NAME,
        device=-1,          # CPU — FinBERT is small enough
        truncation=True,
        max_length=512,
    )
    print("Model loaded")
    print()

    scored = []
    hedges = []
    start = time.time()

    for i in range(0, len(rows), BATCH_SIZE):
        batch = rows[i:i + BATCH_SIZE]
        texts = [r[1][:MAX_CHARS] for r in batch]

        results = clf(texts)
        if isinstance(results, dict):
            results = [results]

        for (seg_id, text), res in zip(batch, results):
            scored.append((seg_id, res["label"].lower(), round(res["score"], 4)))
            hedges.append((seg_id, hedging_density(text)))

        done = len(scored)
        if done % (BATCH_SIZE * 5) == 0 or done == len(rows):
            elapsed = time.time() - start
            rate = done / elapsed if elapsed else 0
            remaining = (len(rows) - done) / rate if rate else 0
            print(f"  {done}/{len(rows)}  ·  {rate:.1f}/sec  "
                  f"·  ~{remaining/60:.1f} min left")

    print()
    print("Writing results to Postgres...")
    store_scores(scored)
    store_hedging(hedges)
    print(f"Done in {(time.time()-start)/60:.1f} minutes")
    print()

    report()


if __name__ == "__main__":
    main()