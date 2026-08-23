"""Add period_type tracking to financial_facts.

XBRL reports quarterly and cumulative (YTD) figures under the SAME
tag, distinguished only by the fact's duration. Without capturing
that, a query mixes them and produces nonsense — a revenue series
that appears to double every quarter.

This adds period_start and period_type so the two can be told apart.
Existing rows are left with NULL period_type until re-pulled.
"""

import sys
import os
import psycopg2

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import settings


def main():
    conn = psycopg2.connect(settings.postgres_url)
    cur = conn.cursor()

    cur.execute("""
        ALTER TABLE financial_facts
        ADD COLUMN IF NOT EXISTS period_start DATE,
        ADD COLUMN IF NOT EXISTS period_type VARCHAR(12)
    """)

    # The old UNIQUE constraint can't distinguish a quarterly fact
    # from a YTD fact with the same end date — they'd collide.
    cur.execute("""
        ALTER TABLE financial_facts
        DROP CONSTRAINT IF EXISTS financial_facts_ticker_metric_period_end_unit_key
    """)
    cur.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS financial_facts_unique_fact
        ON financial_facts (ticker, metric, period_start, period_end, unit)
    """)

    conn.commit()
    cur.close()
    conn.close()
    print("Migration complete: period_start, period_type added")


if __name__ == "__main__":
    main()