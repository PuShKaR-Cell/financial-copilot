"""Normalize speaker labels in transcript_segments.

Three problems this fixes, all found in real Fed transcript data:

1. The Fed Chair changed mid-corpus (Powell -> Warsh), and the title
   changed with them ("CHAIR" -> "CHAIRMAN"). Any query filtering on a
   hardcoded name silently loses events. The fix is a `role` column,
   so downstream code filters on role ("chair") rather than identity.

2. The official transcripts contain typos — 'CHARIMAN WARSH' appears
   alongside 'CHAIRMAN WARSH', splitting one speaker in two. Fuzzy
   title matching catches these.

3. PDF text extraction sometimes injects a space mid-word, producing
   'W ARSH'. Surnames are single tokens, so internal whitespace is
   stripped during normalization.

Safe to re-run.
"""

import os
import sys
from difflib import SequenceMatcher

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import settings

import psycopg2

# Titles that indicate the person chairing the press conference.
# 'chariman' is included deliberately — it appears in the source data.
CHAIR_TITLES = ["chair", "chairman", "chairwoman", "chariman", "chairperson"]


def get_db():
    return psycopg2.connect(settings.postgres_url)


def ensure_columns():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        ALTER TABLE transcript_segments
        ADD COLUMN IF NOT EXISTS role VARCHAR(20),
        ADD COLUMN IF NOT EXISTS speaker_normalized VARCHAR(100)
    """)
    conn.commit()
    cur.close()
    conn.close()


def similar(a, b):
    return SequenceMatcher(None, a, b).ratio()


def classify(speaker):
    """Return (role, normalized_name) for a raw speaker label.

    Role is 'chair' for whoever is chairing, 'press' otherwise.
    The normalized name strips the title and any spurious internal
    spaces, so 'CHAIRMAN WARSH', 'CHARIMAN WARSH', and 'CHAIRMAN W ARSH'
    all collapse to 'WARSH'.
    """
    if not speaker:
        return None, None

    raw = speaker.strip()
    words = raw.split()
    if not words:
        return None, None

    first = words[0].lower()

    # Fuzzy title match catches typos like 'CHARIMAN'
    is_chair = any(similar(first, t) >= 0.8 for t in CHAIR_TITLES)

    if is_chair and len(words) > 1:
        # PDF extraction sometimes injects spaces mid-word ("W ARSH").
        # Surnames are single words, so strip internal whitespace.
        surname = "".join(words[1:])
        return "chair", surname

    return "press", raw


def main():
    ensure_columns()

    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        "SELECT DISTINCT speaker FROM transcript_segments WHERE speaker IS NOT NULL"
    )
    speakers = [r[0] for r in cur.fetchall()]

    updates = 0
    chairs = {}

    for speaker in speakers:
        role, normalized = classify(speaker)
        if role is None:
            continue
        cur.execute("""
            UPDATE transcript_segments
            SET role = %s, speaker_normalized = %s
            WHERE speaker = %s
        """, (role, normalized, speaker))
        updates += cur.rowcount
        if role == "chair":
            chairs.setdefault(normalized, []).append(speaker)

    conn.commit()

    print(f"Normalized {updates} segments")
    print()
    print("── Chairs identified ──")
    for normalized, raw_labels in sorted(chairs.items()):
        print(f"  {normalized}  <-  {raw_labels}")

    print()
    print("── Chair tone by event (normalized) ──")
    cur.execute("""
        SELECT event_id,
               speaker_normalized,
               COUNT(*) AS segs,
               ROUND(AVG(hedging_density), 2) AS hedging,
               ROUND(100.0 * COUNT(*) FILTER (WHERE sentiment='negative')
                     / COUNT(*), 1) AS pct_neg
        FROM transcript_segments
        WHERE role = 'chair' AND source_type = 'official'
        GROUP BY event_id, speaker_normalized
        ORDER BY event_id
    """)
    print(f"  {'event':10} {'chair':10} {'segs':>5} {'hedging':>8} {'% neg':>7}")
    for r in cur.fetchall():
        print(f"  {r[0]:10} {r[1]:10} {r[2]:>5} {r[3]:>8} {r[4]:>7}")

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()