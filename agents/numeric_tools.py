"""Step 26 — Unified numeric tool interface.

Wraps the three numeric capabilities built in Phase 4 behind one
consistent surface for the Numeric Agent (Step 30):

    lookup    -> financial_tools.get_metric / get_ratio / get_growth
    table_qa  -> table_qa.answer
    forecast  -> forecasting.forecast_metric

Every function returns the same envelope:
    {
      "tool":    which tool ran
      "summary": one human-readable sentence the LLM can quote directly
      "data":    the structured result
      "source":  where the number came from (for citations)
    }

That uniform shape is deliberate: the agent can handle any tool's
output the same way, and citations flow through without special-casing.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import settings

from agents import financial_tools
from agents import forecasting
from agents import table_qa


def _fmt(n, unit=""):
    """Human-readable money/number formatting."""
    if n is None:
        return "n/a"
    if unit == "USD/shares":
        return f"${n:.2f}"
    if unit == "percent":
        return f"{n:.1f}%"
    if abs(n) >= 1e9:
        return f"${n/1e9:.2f}B"
    if abs(n) >= 1e6:
        return f"${n/1e6:.1f}M"
    return f"{n:,.0f}"


def lookup(ticker, metric, periods=4, period_type=None):
    """Historical values for a metric — the most common numeric query."""
    result = financial_tools.get_metric(
        ticker, metric, periods=periods, period_type=period_type
    )
    if "error" in result:
        return {"tool": "lookup", "summary": result["error"],
                "data": result, "source": None}

    unit = result["unit"]
    latest = result["values"][0]
    series = ", ".join(
        f"{v['period_end']}: {_fmt(v['value'], unit)}"
        for v in result["values"]
    )
    summary = (
        f"{ticker} {metric} ({result['period_type']}): "
        f"latest {_fmt(latest['value'], unit)} for period ending "
        f"{latest['period_end']}. Recent series — {series}."
    )
    return {
        "tool": "lookup",
        "summary": summary,
        "data": result,
        "source": {"type": "xbrl", "ticker": ticker,
                   "metric": result["xbrl_tag"], "period": latest["period_end"]},
    }


def ratio(ticker, numerator, denominator, periods=4):
    """A ratio between two metrics — margins, mainly."""
    result = financial_tools.get_ratio(ticker, numerator, denominator, periods=periods)
    if "error" in result:
        return {"tool": "ratio", "summary": result["error"],
                "data": result, "source": None}

    latest = result["values"][0]
    series = ", ".join(f"{v['period_end']}: {v['percent']:.1f}%"
                       for v in result["values"])
    summary = (
        f"{ticker} {numerator}/{denominator}: latest {latest['percent']:.1f}% "
        f"for period ending {latest['period_end']}. Recent — {series}."
    )
    return {
        "tool": "ratio",
        "summary": summary,
        "data": result,
        "source": {"type": "xbrl_derived", "ticker": ticker,
                   "period": latest["period_end"]},
    }


def growth(ticker, metric, periods=4):
    """Period-over-period growth rates."""
    result = financial_tools.get_growth(ticker, metric, periods=periods)
    if "error" in result:
        return {"tool": "growth", "summary": result["error"],
                "data": result, "source": None}

    latest = result["growth"][0]
    series = ", ".join(f"{g['period_end']}: {g['growth_pct']:+.1f}%"
                       for g in result["growth"])
    summary = (
        f"{ticker} {metric} growth: most recent {latest['growth_pct']:+.1f}% "
        f"(period ending {latest['period_end']}). Recent — {series}."
    )
    return {
        "tool": "growth",
        "summary": summary,
        "data": result,
        "source": {"type": "xbrl_derived", "ticker": ticker,
                   "period": latest["period_end"]},
    }


def forecast(ticker, metric, period_type="quarterly"):
    """Next-quarter forecast, with a reliability caveat baked into the summary."""
    result = forecasting.forecast_metric(ticker, metric, period_type=period_type)
    if "error" in result:
        return {"tool": "forecast", "summary": result["error"],
                "data": result, "source": None}

    unit = result["unit"]
    value = _fmt(result["forecast"], unit)

    if result.get("reliable"):
        confidence = "high confidence"
        summary = (
            f"{ticker} {metric} next-quarter forecast: {value} "
            f"({confidence}, {result['recommended_method']} method, "
            f"backtest error {result['backtest_errors'].get(result['recommended_method'], 0):.1%})."
        )
    else:
        confidence = "low confidence"
        summary = (
            f"{ticker} {metric} is too volatile to forecast reliably "
            f"(best backtest error "
            f"{result['backtest_errors'].get(result['recommended_method'], 0):.0%}). "
            f"Point estimate {value}, but treat with caution."
        )

    return {
        "tool": "forecast",
        "summary": summary,
        "data": result,
        "source": {"type": "forecast", "ticker": ticker,
                   "method": result["recommended_method"],
                   "reliable": result.get("reliable", False)},
    }


def read_table(question, ticker=None, validate_metric=None):
    """Extract a figure from a filing table (with XBRL validation if asked)."""
    result = table_qa.answer(
        question, ticker=ticker, validate_metric=validate_metric, verbose=False
    )
    if "error" in result:
        return {"tool": "table_qa", "summary": result["error"],
                "data": result, "source": None}

    ans = result["answer"]
    summary = f"From {result['source']['ticker']} filing: {ans.get('note', '')}"

    validation_note = ""
    if "validation" in result and result["validation"].get("checked"):
        v = result["validation"]
        validation_note = (" [XBRL-verified]" if v["match"]
                           else f" [WARNING: differs from XBRL by {v['relative_diff']:.0%}]")
    summary += validation_note

    return {
        "tool": "table_qa",
        "summary": summary,
        "data": result,
        "source": {"type": "filing_table", **result["source"]},
    }


# ── Tool registry ──────────────────────────────────────────
# The Numeric Agent (Step 30) uses this to know what it can call.

TOOLS = {
    "lookup": {
        "fn": lookup,
        "description": "Get historical values of a financial metric",
        "args": "ticker, metric, periods=4",
    },
    "ratio": {
        "fn": ratio,
        "description": "Compute a ratio (e.g. gross margin) between two metrics",
        "args": "ticker, numerator, denominator, periods=4",
    },
    "growth": {
        "fn": growth,
        "description": "Period-over-period growth rate of a metric",
        "args": "ticker, metric, periods=4",
    },
    "forecast": {
        "fn": forecast,
        "description": "Forecast next quarter, with reliability caveat",
        "args": "ticker, metric",
    },
    "read_table": {
        "fn": read_table,
        "description": "Extract a specific figure from a filing table",
        "args": "question, ticker, validate_metric",
    },
}


def available_metrics():
    return financial_tools.list_metrics()


def available_companies():
    return financial_tools.list_companies()


# ── Demo ───────────────────────────────────────────────────

def main():
    print("Available tools:")
    for name, spec in TOOLS.items():
        print(f"  {name:12} ({spec['args']})")
        print(f"               {spec['description']}")
    print()
    print("Metrics:", ", ".join(available_metrics()))
    print()

    print("=" * 60)
    print("  lookup('MSFT', 'revenue')")
    print("=" * 60)
    print(" ", lookup("MSFT", "revenue")["summary"])
    print()

    print("=" * 60)
    print("  ratio('MSFT', 'gross_profit', 'revenue')")
    print("=" * 60)
    print(" ", ratio("MSFT", "gross_profit", "revenue")["summary"])
    print()

    print("=" * 60)
    print("  growth('CRM', 'revenue')")
    print("=" * 60)
    print(" ", growth("CRM", "revenue")["summary"])
    print()

    print("=" * 60)
    print("  forecast('MSFT', 'revenue')   [reliable]")
    print("=" * 60)
    print(" ", forecast("MSFT", "revenue")["summary"])
    print()

    print("=" * 60)
    print("  forecast('GOOGL', 'net_income')   [should warn]")
    print("=" * 60)
    print(" ", forecast("GOOGL", "net_income")["summary"])
    print()


if __name__ == "__main__":
    main()