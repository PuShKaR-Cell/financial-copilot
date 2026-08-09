"""Step 7 — Pull structured XBRL financial facts.

Uses the EDGAR XBRL "company facts" API to get machine-readable
financial data (revenue, net income, EPS, assets, etc.) for each
tracked company.

How it works:
1. For each company, hits the XBRL company-facts endpoint
2. Gets back a big JSON with every tagged financial metric
3. Filters for USD-denominated facts filed via 10-K or 10-Q
4. Stores each fact as a row in the financial_facts Postgres table

These become your ground truth — when your system says
"revenue was $X", the eval harness checks it against these numbers.
"""

import os
import sys
import time
import yaml
import requests
import psycopg2

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import settings

# ── Constants ──────────────────────────────────────────────

COMPANY_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
REQUEST_DELAY = 0.15

# Key financial metrics to store (there are hundreds in XBRL,
# but these are the ones most useful for analyst-style questions)
KEY_METRICS = {
    # Income statement
    "Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax",
    "CostOfRevenue", "CostOfGoodsAndServicesSold",
    "GrossProfit", "OperatingIncome", "OperatingIncomeLoss",
    "NetIncomeLoss", "EarningsPerShareBasic", "EarningsPerShareDiluted",
    # Balance sheet
    "Assets", "Liabilities", "StockholdersEquity",
    "CashAndCashEquivalentsAtCarryingValue",
    "LongTermDebt", "LongTermDebtNoncurrent",
    # Cash flow
    "NetCashProvidedByUsedInOperatingActivities",
    "NetCashProvidedByUsedInFinancingActivities",
    "NetCashProvidedByUsedInInvestingActivities",
    # Margins / ratios (sometimes tagged directly)
    "OperatingExpenses", "ResearchAndDevelopmentExpense",
    "SellingGeneralAndAdministrativeExpense",
}


def get_headers():
    ua = settings.edgar_user_agent
    if not ua:
        print("ERROR: EDGAR_USER_AGENT not set in .env")
        sys.exit(1)
    return {"User-Agent": ua}


def load_companies():
    with open("companies.yaml", "r") as f:
        data = yaml.safe_load(f)
    return data["companies"]


def fetch_company_facts(cik, headers):
    """Get all XBRL facts for one company."""
    url = COMPANY_FACTS_URL.format(cik=cik)
    response = requests.get(url, headers=headers)
    response.raise_for_status()
    time.sleep(REQUEST_DELAY)
    return response.json()


def extract_facts(facts_data, ticker):
    """Pull out key financial metrics from the XBRL response.

    Returns a list of tuples ready for database insertion.
    """
    rows = []
    us_gaap = facts_data.get("facts", {}).get("us-gaap", {})

    for metric_name, metric_data in us_gaap.items():
        if metric_name not in KEY_METRICS:
            continue

        units = metric_data.get("units", {})

        # Get USD values (or "shares" for EPS-type metrics)
        for unit_type in ["USD", "USD/shares"]:
            if unit_type not in units:
                continue

            for entry in units[unit_type]:
                form = entry.get("form", "")
                if form not in ("10-K", "10-Q"):
                    continue

                rows.append((
                    ticker,
                    metric_name,
                    entry.get("end"),         # period end date
                    entry.get("fp"),          # fiscal period label (FY, Q1, Q2...)
                    entry.get("val"),         # the actual number
                    unit_type,
                    form,
                    entry.get("filed"),       # date it was filed with SEC
                    entry.get("accn"),        # accession number
                ))

    return rows


def store_facts(rows):
    """Insert facts into Postgres, skipping duplicates."""
    conn = psycopg2.connect(settings.postgres_url)
    cur = conn.cursor()

    inserted = 0
    skipped = 0

    for row in rows:
        try:
            cur.execute("""
                INSERT INTO financial_facts
                    (ticker, metric, period_end, period_label, value, unit, form, filed, accession)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (ticker, metric, period_end, unit) DO NOTHING
            """, row)
            if cur.rowcount > 0:
                inserted += 1
            else:
                skipped += 1
        except Exception as e:
            conn.rollback()
            print(f"    Warning: {e}")
            continue

    conn.commit()
    cur.close()
    conn.close()
    return inserted, skipped


def main():
    headers = get_headers()
    companies = load_companies()

    print(f"Pulling XBRL facts for {len(companies)} companies...")
    print(f"Tracking {len(KEY_METRICS)} key metrics")
    print()

    grand_inserted = 0
    grand_skipped = 0

    for company in companies:
        ticker = company["ticker"]
        cik = company["cik"]
        name = company.get("name", ticker)

        print(f"── {name} ({ticker}) ──")

        try:
            data = fetch_company_facts(cik, headers)
            rows = extract_facts(data, ticker)
            inserted, skipped = store_facts(rows)
            grand_inserted += inserted
            grand_skipped += skipped
            print(f"  {inserted} new facts, {skipped} already existed")

        except requests.exceptions.HTTPError as e:
            print(f"  ERROR: {e}")
        except Exception as e:
            print(f"  ERROR: {e}")

        print()

    print(f"Done! Inserted: {grand_inserted}, Skipped: {grand_skipped}")


if __name__ == "__main__":
    main()