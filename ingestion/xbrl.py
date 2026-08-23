"""Step 7 (revised) — Pull structured XBRL financial facts.

Now captures the DURATION of each fact, not just its end date.

XBRL reports the same tag at multiple durations: a 10-Q contains
both the quarter's revenue (~90 days) and the year-to-date figure
(~180/270 days). Storing only the end date makes these
indistinguishable, so queries silently mix them.

period_type is derived from (period_end - period_start):
    <= 100 days  -> quarterly
    <= 300 days  -> ytd
    else         -> annual
Instant facts (balance sheet items with no start date) -> instant
"""

import os
import sys
import time
import yaml
import requests
import psycopg2
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import settings

COMPANY_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
REQUEST_DELAY = 0.15

KEY_METRICS = {
    "Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax",
    "CostOfRevenue", "CostOfGoodsAndServicesSold",
    "GrossProfit", "OperatingIncome", "OperatingIncomeLoss",
    "NetIncomeLoss", "EarningsPerShareBasic", "EarningsPerShareDiluted",
    "Assets", "Liabilities", "StockholdersEquity",
    "CashAndCashEquivalentsAtCarryingValue",
    "LongTermDebt", "LongTermDebtNoncurrent",
    "NetCashProvidedByUsedInOperatingActivities",
    "NetCashProvidedByUsedInFinancingActivities",
    "NetCashProvidedByUsedInInvestingActivities",
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
        return yaml.safe_load(f)["companies"]


def fetch_company_facts(cik, headers):
    url = COMPANY_FACTS_URL.format(cik=cik)
    r = requests.get(url, headers=headers)
    r.raise_for_status()
    time.sleep(REQUEST_DELAY)
    return r.json()


def classify_period(start_str, end_str):
    """Derive (period_start, period_type) from an XBRL fact's dates.

    Facts without a start date are point-in-time balance sheet
    values ("instant"). Facts with a start date are durations,
    bucketed by length.
    """
    if not start_str:
        return None, "instant"

    try:
        start = datetime.strptime(start_str, "%Y-%m-%d").date()
        end = datetime.strptime(end_str, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None, None

    days = (end - start).days

    if days <= 100:
        return start, "quarterly"
    if days <= 300:
        return start, "ytd"
    return start, "annual"


def extract_facts(facts_data, ticker):
    rows = []
    us_gaap = facts_data.get("facts", {}).get("us-gaap", {})

    for metric_name, metric_data in us_gaap.items():
        if metric_name not in KEY_METRICS:
            continue

        for unit_type in ("USD", "USD/shares"):
            for entry in metric_data.get("units", {}).get(unit_type, []):
                if entry.get("form") not in ("10-K", "10-Q"):
                    continue

                period_start, period_type = classify_period(
                    entry.get("start"), entry.get("end")
                )
                if period_type is None:
                    continue

                rows.append((
                    ticker, metric_name,
                    period_start, entry.get("end"), period_type,
                    entry.get("fp"), entry.get("val"), unit_type,
                    entry.get("form"), entry.get("filed"), entry.get("accn"),
                ))

    return rows


def store_facts(rows):
    conn = psycopg2.connect(settings.postgres_url)
    cur = conn.cursor()
    inserted = skipped = 0

    for row in rows:
        try:
            cur.execute("""
                INSERT INTO financial_facts
                    (ticker, metric, period_start, period_end, period_type,
                     period_label, value, unit, form, filed, accession)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (ticker, metric, period_start, period_end, unit)
                DO UPDATE SET period_type = EXCLUDED.period_type
            """, row)
            if cur.rowcount > 0:
                inserted += 1
            else:
                skipped += 1
        except Exception:
            conn.rollback()
            continue

    conn.commit()
    cur.close()
    conn.close()
    return inserted, skipped


def main():
    headers = get_headers()
    companies = load_companies()

    print(f"Pulling XBRL facts for {len(companies)} companies")
    print("Now capturing fact duration (quarterly / ytd / annual / instant)")
    print()

    total_in = total_skip = 0

    for company in companies:
        ticker = company["ticker"]
        print(f"── {company.get('name', ticker)} ({ticker}) ──")
        try:
            data = fetch_company_facts(company["cik"], headers)
            rows = extract_facts(data, ticker)
            ins, skip = store_facts(rows)
            total_in += ins
            total_skip += skip
            print(f"  {ins} new, {skip} existing")
        except Exception as e:
            print(f"  ERROR: {e}")

    print()
    print(f"Done! Inserted: {total_in}, Skipped: {total_skip}")
    print()

    # Show the distribution that was previously invisible
    conn = psycopg2.connect(settings.postgres_url)
    cur = conn.cursor()
    cur.execute("""
        SELECT period_type, COUNT(*)
        FROM financial_facts
        GROUP BY period_type
        ORDER BY COUNT(*) DESC
    """)
    print("── Facts by period type ──")
    for ptype, count in cur.fetchall():
        print(f"  {str(ptype):10} {count:>7,}")
    cur.close()
    conn.close()


if __name__ == "__main__":
    main()