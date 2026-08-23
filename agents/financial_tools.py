"""Step 23 — Financial facts query layer.

A clean, callable interface over the XBRL facts in Postgres.
These functions become tools for the Numeric Agent (Step 30).

Design note: the LLM calls these functions rather than writing SQL.
Named functions with validated parameters are more reliable than
generated queries, easier to unit-test, and can't produce a malformed
statement that errors at runtime.

Two XBRL quirks this layer handles:

1. Tag variance — some filers report "Revenues", others report
   "RevenueFromContractWithCustomerExcludingAssessedTax" for the
   same concept. METRIC_ALIASES maps friendly names onto the tag
   variants seen in the data, trying each in turn.

2. Duration mixing — XBRL reports quarterly AND cumulative (YTD)
   figures under the SAME tag, distinguished only by the fact's
   duration. Querying without filtering on period_type silently
   mixes them, producing a revenue series that appears to double
   each quarter then collapse at fiscal year end. Every query here
   defaults to period_type="quarterly".
"""

import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import settings

import psycopg2


# ── Metric aliases ─────────────────────────────────────────
# Friendly name -> ordered list of XBRL tags to try.
# Order matters: the first tag with data wins.

METRIC_ALIASES = {
    "revenue": [
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
    ],
    "cost_of_revenue": [
        "CostOfRevenue",
        "CostOfGoodsAndServicesSold",
    ],
    "gross_profit": ["GrossProfit"],
    "operating_income": ["OperatingIncomeLoss", "OperatingIncome"],
    "net_income": ["NetIncomeLoss"],
    "eps_basic": ["EarningsPerShareBasic"],
    "eps_diluted": ["EarningsPerShareDiluted"],
    "assets": ["Assets"],
    "liabilities": ["Liabilities"],
    "equity": ["StockholdersEquity"],
    "cash": ["CashAndCashEquivalentsAtCarryingValue"],
    "long_term_debt": ["LongTermDebtNoncurrent", "LongTermDebt"],
    "operating_cash_flow": ["NetCashProvidedByUsedInOperatingActivities"],
    "investing_cash_flow": ["NetCashProvidedByUsedInInvestingActivities"],
    "financing_cash_flow": ["NetCashProvidedByUsedInFinancingActivities"],
    "operating_expenses": ["OperatingExpenses"],
    "rnd_expense": ["ResearchAndDevelopmentExpense"],
    "sga_expense": ["SellingGeneralAndAdministrativeExpense"],
}

# Balance sheet items are point-in-time values, not durations.
# XBRL stores them as "instant" facts with no start date, so
# asking for them as "quarterly" returns nothing.
INSTANT_METRICS = {
    "assets", "liabilities", "equity", "cash", "long_term_debt",
}

VALID_PERIOD_TYPES = {"quarterly", "ytd", "annual", "instant"}


def get_db():
    return psycopg2.connect(settings.postgres_url)


def list_metrics():
    """Return the friendly metric names this layer understands."""
    return sorted(METRIC_ALIASES.keys())


def list_companies():
    """Return tickers that have financial data available."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT ticker FROM financial_facts ORDER BY ticker")
    tickers = [r[0] for r in cur.fetchall()]
    cur.close()
    conn.close()
    return tickers


def default_period_type(metric_key):
    """Balance sheet metrics are instants; income/cash flow are durations."""
    return "instant" if metric_key in INSTANT_METRICS else "quarterly"


def get_metric(ticker, metric, periods=4, form=None, period_type=None):
    """Get the most recent values for one metric.

    Args:
        ticker:      company ticker, e.g. "MSFT"
        metric:      friendly name from list_metrics(), e.g. "revenue"
        periods:     how many periods back to return
        form:        optionally restrict to "10-K" or "10-Q"
        period_type: "quarterly" (default for flow metrics), "ytd",
                     "annual", or "instant" (default for balance sheet
                     metrics). Pass None to use the sensible default.

    Returns:
        {"ticker", "metric", "xbrl_tag", "unit", "period_type", "values": [...]}
        where each value is {"period_end", "period_label", "value", "form"}.
        Returns an "error" key if the metric is unknown or has no data.
    """
    ticker = ticker.upper().strip()
    metric_key = metric.lower().strip()

    if metric_key not in METRIC_ALIASES:
        return {
            "error": f"Unknown metric {metric!r}",
            "available_metrics": list_metrics(),
        }

    if period_type is None:
        period_type = default_period_type(metric_key)

    if period_type not in VALID_PERIOD_TYPES:
        return {
            "error": f"Invalid period_type {period_type!r}",
            "valid_period_types": sorted(VALID_PERIOD_TYPES),
        }

    conn = get_db()
    cur = conn.cursor()

    # Try each XBRL tag variant until one returns data
    for tag in METRIC_ALIASES[metric_key]:
        sql = """
            SELECT period_end, period_label, value, unit, form, period_type
            FROM financial_facts
            WHERE ticker = %s AND metric = %s AND period_type = %s
        """
        params = [ticker, tag, period_type]

        if form:
            sql += " AND form = %s"
            params.append(form.upper())

        sql += " ORDER BY period_end DESC LIMIT %s"
        params.append(periods)

        cur.execute(sql, params)
        rows = cur.fetchall()

        if rows:
            cur.close()
            conn.close()
            return {
                "ticker": ticker,
                "metric": metric_key,
                "xbrl_tag": tag,
                "unit": rows[0][3],
                "period_type": period_type,
                "values": [
                    {
                        "period_end": r[0].isoformat() if isinstance(r[0], date) else str(r[0]),
                        "period_label": r[1],
                        "value": float(r[2]) if r[2] is not None else None,
                        "form": r[4],
                    }
                    for r in rows
                ],
            }

    cur.close()
    conn.close()
    return {
        "error": f"No {period_type} data for {metric_key!r} on {ticker}",
        "tags_tried": METRIC_ALIASES[metric_key],
    }


def get_ratio(ticker, numerator, denominator, periods=4, period_type=None):
    """Compute a ratio between two metrics, period by period.

    Useful for margins: get_ratio("MSFT", "gross_profit", "revenue")
    returns gross margin as a percentage for each period.

    Only periods where BOTH metrics have data are returned.
    """
    num = get_metric(ticker, numerator, periods=periods * 3, period_type=period_type)
    den = get_metric(ticker, denominator, periods=periods * 3, period_type=period_type)

    if "error" in num:
        return num
    if "error" in den:
        return den

    # Index denominator by period so we can match them up
    den_by_period = {v["period_end"]: v["value"] for v in den["values"]}

    results = []
    for v in num["values"]:
        d = den_by_period.get(v["period_end"])
        if d is None or d == 0 or v["value"] is None:
            continue
        results.append({
            "period_end": v["period_end"],
            "period_label": v["period_label"],
            "numerator": v["value"],
            "denominator": d,
            "ratio": round(v["value"] / d, 6),
            "percent": round(v["value"] / d * 100, 2),
        })
        if len(results) >= periods:
            break

    if not results:
        return {
            "error": f"No overlapping periods for {numerator}/{denominator} on {ticker}"
        }

    return {
        "ticker": ticker,
        "numerator_metric": numerator,
        "denominator_metric": denominator,
        "period_type": num["period_type"],
        "values": results,
    }


def compare_companies(tickers, metric, periods=1, period_type=None):
    """Get the same metric across several companies for comparison."""
    out = {"metric": metric, "companies": {}}
    for t in tickers:
        result = get_metric(t, metric, periods=periods, period_type=period_type)
        if "error" in result:
            out["companies"][t.upper()] = {"error": result["error"]}
        else:
            out["companies"][t.upper()] = {
                "unit": result["unit"],
                "period_type": result["period_type"],
                "values": result["values"],
            }
    return out


def get_growth(ticker, metric, periods=4, period_type=None):
    """Period-over-period growth rates for a metric.

    Note this is sequential (quarter vs prior quarter), not
    year-over-year. For seasonal businesses, YoY is often the
    more meaningful comparison — use periods=5 and compare the
    first and last entries, or query period_type="annual".
    """
    result = get_metric(ticker, metric, periods=periods + 1, period_type=period_type)
    if "error" in result:
        return result

    values = result["values"]  # newest first
    if len(values) < 2:
        return {"error": f"Need at least 2 periods; got {len(values)}"}

    growth = []
    for i in range(len(values) - 1):
        curr = values[i]["value"]
        prev = values[i + 1]["value"]
        if prev is None or prev == 0 or curr is None:
            continue
        growth.append({
            "period_end": values[i]["period_end"],
            "period_label": values[i]["period_label"],
            "value": curr,
            "previous_value": prev,
            "growth_pct": round((curr - prev) / abs(prev) * 100, 2),
        })

    return {
        "ticker": result["ticker"],
        "metric": result["metric"],
        "unit": result["unit"],
        "period_type": result["period_type"],
        "growth": growth,
    }


# ── Demo ───────────────────────────────────────────────────

def fmt(n, unit):
    """Human-readable number formatting."""
    if n is None:
        return "n/a"
    if unit == "USD/shares":
        return f"${n:.2f}"
    if abs(n) >= 1e9:
        return f"${n/1e9:.2f}B"
    if abs(n) >= 1e6:
        return f"${n/1e6:.1f}M"
    return f"${n:,.0f}"


def main():
    print("Companies with data:", ", ".join(list_companies()))
    print()
    print("Metrics available:", ", ".join(list_metrics()))
    print()

    print("=" * 58)
    print("  get_metric('MSFT', 'revenue')  — quarterly")
    print("=" * 58)
    r = get_metric("MSFT", "revenue", periods=4)
    if "error" in r:
        print(" ", r["error"])
    else:
        print(f"  XBRL tag: {r['xbrl_tag']}   period_type: {r['period_type']}")
        for v in r["values"]:
            print(f"    {v['period_end']}  {v['period_label'] or '-':4}  "
                  f"{fmt(v['value'], r['unit']):>12}  ({v['form']})")
    print()

    print("=" * 58)
    print("  get_metric('MSFT', 'revenue', period_type='annual')")
    print("=" * 58)
    r = get_metric("MSFT", "revenue", periods=3, period_type="annual")
    if "error" in r:
        print(" ", r["error"])
    else:
        for v in r["values"]:
            print(f"    {v['period_end']}  {fmt(v['value'], r['unit']):>12}")
    print()

    print("=" * 58)
    print("  get_ratio('MSFT', 'gross_profit', 'revenue')  -> gross margin")
    print("=" * 58)
    r = get_ratio("MSFT", "gross_profit", "revenue", periods=4)
    if "error" in r:
        print(" ", r["error"])
    else:
        for v in r["values"]:
            print(f"    {v['period_end']}  {v['percent']:>6.2f}%")
    print()

    print("=" * 58)
    print("  get_growth('CRM', 'revenue')  — sequential quarterly")
    print("=" * 58)
    r = get_growth("CRM", "revenue", periods=4)
    if "error" in r:
        print(" ", r["error"])
    else:
        for g in r["growth"]:
            print(f"    {g['period_end']}  {fmt(g['value'], r['unit']):>12}  "
                  f"{g['growth_pct']:>+7.2f}%")
    print()

    print("=" * 58)
    print("  get_metric('MSFT', 'cash')  — instant / balance sheet")
    print("=" * 58)
    r = get_metric("MSFT", "cash", periods=4)
    if "error" in r:
        print(" ", r["error"])
    else:
        print(f"  period_type: {r['period_type']}")
        for v in r["values"]:
            print(f"    {v['period_end']}  {fmt(v['value'], r['unit']):>12}")
    print()

    print("=" * 58)
    print("  compare_companies(['MSFT','GOOGL','CRM'], 'net_income')")
    print("=" * 58)
    r = compare_companies(["MSFT", "GOOGL", "CRM"], "net_income", periods=1)
    for ticker, data in r["companies"].items():
        if "error" in data:
            print(f"    {ticker:6}  {data['error']}")
        else:
            v = data["values"][0]
            print(f"    {ticker:6}  {v['period_end']}  "
                  f"{fmt(v['value'], data['unit']):>12}")


if __name__ == "__main__":
    main()