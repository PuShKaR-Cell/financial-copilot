"""Create the database tables for financial facts and market data.

Run this once before running xbrl.py or market_data.py.
Safe to run multiple times — it won't destroy existing data.
"""

import sys
import os
import psycopg2

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import settings


def create_tables():
    conn = psycopg2.connect(settings.postgres_url)
    cur = conn.cursor()

    # Financial facts from XBRL (Step 7)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS financial_facts (
            id SERIAL PRIMARY KEY,
            ticker VARCHAR(10) NOT NULL,
            metric VARCHAR(200) NOT NULL,
            period_end DATE,
            period_label VARCHAR(20),
            value NUMERIC,
            unit VARCHAR(50),
            form VARCHAR(10),
            filed DATE,
            accession VARCHAR(30),
            UNIQUE(ticker, metric, period_end, unit)
        );
    """)

    # Market/macro data (Step 8)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS market_data (
            id SERIAL PRIMARY KEY,
            ticker VARCHAR(20) NOT NULL,
            date DATE NOT NULL,
            open NUMERIC,
            high NUMERIC,
            low NUMERIC,
            close NUMERIC,
            volume BIGINT,
            UNIQUE(ticker, date)
        );
    """)

    # Ingestion manifest (Step 10)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS ingestion_manifest (
            id SERIAL PRIMARY KEY,
            source VARCHAR(50) NOT NULL,
            source_id VARCHAR(200) NOT NULL,
            ingested_at TIMESTAMP DEFAULT NOW(),
            source_hash VARCHAR(64),
            UNIQUE(source, source_id)
        );
    """)

    conn.commit()
    cur.close()
    conn.close()
    print("Tables created successfully")


if __name__ == "__main__":
    create_tables()