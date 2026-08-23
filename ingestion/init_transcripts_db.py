"""Create the transcript_segments table for Phase 3.

Holds both ASR output (from Whisper) and official transcript text,
distinguished by source_type, so downstream steps can treat them
uniformly or filter to one.
"""

import sys
import os
import psycopg2

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import settings


def create_tables():
    conn = psycopg2.connect(settings.postgres_url)
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS transcript_segments (
            id SERIAL PRIMARY KEY,
            event_id VARCHAR(50) NOT NULL,
            source_type VARCHAR(20) NOT NULL,
            segment_index INTEGER NOT NULL,
            speaker VARCHAR(100),
            text TEXT NOT NULL,
            start_sec NUMERIC,
            end_sec NUMERIC,
            sentiment VARCHAR(20),
            sentiment_score NUMERIC,
            UNIQUE(event_id, source_type, segment_index)
        );
    """)

    conn.commit()
    cur.close()
    conn.close()
    print("transcript_segments table ready")


if __name__ == "__main__":
    create_tables()