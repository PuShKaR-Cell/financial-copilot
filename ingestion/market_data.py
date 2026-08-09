"""Step 8 — Pull market and macro data.

Two data sources:
1. yfinance — daily price/volume history for each tracked company
2. FRED API — macro indicators (Fed funds rate, CPI) for context

Price data goes into the market_data Postgres table.
Macro series go into the same table with the series ID as the "ticker"
(e.g. ticker="FEDFUNDS", ticker="CPIAUCSL").

yfinance needs no API key. FRED needs a free key in .env — if missing,
the script skips macro data and just pulls prices.
"""

import os
import sys
import yaml
import requests
import psycopg2
import yfinance as yf
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import settings

# ── Constants ──────────────────────────────────────────────

LOOKBACK_YEARS = 2
FRED_SERIES = {
    "FEDFUNDS": "Federal Funds Effective Rate",
    "CPIAUCSL": "Consumer Price Index (All Urban)",
}
FRED_URL = "https://api.stlouisfed.org/fred/series/observations"


def load_companies():
    with open("companies.yaml", "r") as f:
        data = yaml.safe_load(f)
    return data["companies"]


def pull_stock_prices(ticker, start_date):
    """Download daily price history for one ticker via yfinance.

    Returns a list of tuples: (ticker, date, open, high, low, close, volume)
    """
    try:
        stock = yf.Ticker(ticker)
        df = stock.history(start=start_date, auto_adjust=True)

        if df.empty:
            return []

        rows = []
        for date, row in df.iterrows():
            rows.append((
                ticker,
                date.strftime("%Y-%m-%d"),
                round(float(row["Open"]), 4),
                round(float(row["High"]), 4),
                round(float(row["Low"]), 4),
                round(float(row["Close"]), 4),
                int(row["Volume"]),
            ))
        return rows

    except Exception as e:
        print(f"  ERROR pulling {ticker}: {e}")
        return []


def pull_fred_series(series_id, start_date, api_key):
    """Download one macro series from FRED.

    Returns a list of tuples in the same format as stock prices,
    but with open/high/low set to None (only close is meaningful).
    """
    params = {
        "series_id": series_id,
        "api_key": api_key,
        "file_type": "json",
        "observation_start": start_date,
    }

    try:
        response = requests.get(FRED_URL, params=params)
        response.raise_for_status()
        data = response.json()

        rows = []
        for obs in data.get("observations", []):
            if obs["value"] == ".":  # FRED uses "." for missing data
                continue
            rows.append((
                series_id,
                obs["date"],
                None,  # open
                None,  # high
                None,  # low
                float(obs["value"]),  # close
                None,  # volume
            ))
        return rows

    except Exception as e:
        print(f"  ERROR pulling {series_id}: {e}")
        return []


def store_market_data(rows):
    """Insert market data rows into Postgres, skipping duplicates."""
    conn = psycopg2.connect(settings.postgres_url)
    cur = conn.cursor()

    inserted = 0
    skipped = 0

    for row in rows:
        try:
            cur.execute("""
                INSERT INTO market_data (ticker, date, open, high, low, close, volume)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (ticker, date) DO NOTHING
            """, row)
            if cur.rowcount > 0:
                inserted += 1
            else:
                skipped += 1
        except Exception as e:
            conn.rollback()
            continue

    conn.commit()
    cur.close()
    conn.close()
    return inserted, skipped


def main():
    companies = load_companies()
    start_date = (datetime.now() - timedelta(days=365 * LOOKBACK_YEARS)).strftime("%Y-%m-%d")

    print(f"Pulling {LOOKBACK_YEARS} years of data (from {start_date})")
    print()

    # ── Stock prices ──────────────────────────────────
    print("=== Stock Prices ===")
    print()

    total_inserted = 0
    total_skipped = 0

    for company in companies:
        ticker = company["ticker"]
        name = company.get("name", ticker)
        print(f"── {name} ({ticker}) ──")

        rows = pull_stock_prices(ticker, start_date)
        if rows:
            inserted, skipped = store_market_data(rows)
            total_inserted += inserted
            total_skipped += skipped
            print(f"  {inserted} new days, {skipped} already existed")
        else:
            print(f"  No data returned")
        print()

    print(f"Stock totals — Inserted: {total_inserted}, Skipped: {total_skipped}")
    print()

    # ── Macro series ──────────────────────────────────
    fred_key = settings.fred_api_key
    if not fred_key:
        print("=== FRED Macro Data ===")
        print("  Skipping — no FRED_API_KEY in .env (add one later and re-run)")
        print()
    else:
        print("=== FRED Macro Data ===")
        print()

        for series_id, description in FRED_SERIES.items():
            print(f"── {description} ({series_id}) ──")
            rows = pull_fred_series(series_id, start_date, fred_key)
            if rows:
                inserted, skipped = store_market_data(rows)
                total_inserted += inserted
                total_skipped += skipped
                print(f"  {inserted} new observations, {skipped} already existed")
            else:
                print(f"  No data returned")
            print()

    print(f"All done! Total inserted: {total_inserted}, Total skipped: {total_skipped}")


if __name__ == "__main__":
    main()