"""Step 25 — Financial metric forecasting.

Forecasts the next 1-2 quarters of a metric from its history, and —
critically — always reports how each method compares to a naive
baseline (next quarter = last quarter).

Why no Chronos/TimesFM: those foundation models need a GPU and assume
long series. Quarterly financials give ~8 points with strong trend and
seasonality; lightweight statistical methods fit this regime better and
don't overfit. The right tool is the one that beats the baseline on
YOUR data, which is why every method is scored against it here.

Methods:
  naive           — next = last value (the baseline to beat)
  drift           — linear extrapolation of the average change
  seasonal_naive  — next = same quarter last year (for seasonal metrics)
  holt            — double exponential smoothing (trend, no season)

Backtesting: each method is evaluated by hiding the most recent point,
predicting it, and measuring the error. The method with the lowest
backtest error is recommended — but only if it beats naive.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import settings

from agents import financial_tools


def naive(history):
    """Next = last value. The baseline every method must beat."""
    return history[-1]


def drift(history):
    """Linear extrapolation of the average period-over-period change."""
    if len(history) < 2:
        return history[-1]
    total_change = history[-1] - history[0]
    avg_change = total_change / (len(history) - 1)
    return history[-1] + avg_change


def seasonal_naive(history, season=4):
    """Next = value from one season (4 quarters) ago.

    Strong for revenue in seasonal businesses, where Q4 looks like
    last Q4 more than it looks like Q3.
    """
    if len(history) < season:
        return history[-1]
    return history[-season]


def holt(history, alpha=0.6, beta=0.3):
    """Double exponential smoothing — captures level and trend.

    alpha weights recent level, beta weights recent trend.
    Values chosen to favor recent data on short series.
    """
    if len(history) < 2:
        return history[-1]

    level = history[0]
    trend = history[1] - history[0]

    for value in history[1:]:
        prev_level = level
        level = alpha * value + (1 - alpha) * (level + trend)
        trend = beta * (level - prev_level) + (1 - beta) * trend

    return level + trend


METHODS = {
    "naive": naive,
    "drift": drift,
    "seasonal_naive": seasonal_naive,
    "holt": holt,
}


def backtest(history, method_fn):
    """Hide the last point, predict it, return absolute % error.

    This is how we know whether a method actually works on this
    series rather than just producing a plausible-looking number.
    """
    if len(history) < 3:
        return None
    train = history[:-1]
    actual = history[-1]
    predicted = method_fn(train)
    if actual == 0:
        return None
    return abs(predicted - actual) / abs(actual)


def forecast_metric(ticker, metric, horizon=1, period_type="quarterly"):
    """Forecast a metric, comparing all methods against the baseline.

    Returns the recommended forecast (best backtested method, but only
    if it beats naive), all method predictions, and the backtest errors
    that justify the choice.
    """
    data = financial_tools.get_metric(
        ticker, metric, periods=12, period_type=period_type
    )
    if "error" in data:
        return data

    # get_metric returns newest-first; forecasting needs oldest-first
    values = [v["value"] for v in reversed(data["values"]) if v["value"] is not None]

    if len(values) < 3:
        return {
            "error": f"Need at least 3 periods to forecast; have {len(values)}",
            "ticker": ticker, "metric": metric,
        }

    # Predict and backtest each method
    predictions = {}
    backtests = {}
    for name, fn in METHODS.items():
        predictions[name] = fn(values)
        err = backtest(values, fn)
        if err is not None:
            backtests[name] = err

    # Pick the method with the lowest backtest error
    if backtests:
        best_method = min(backtests, key=backtests.get)
        naive_error = backtests.get("naive", float("inf"))
        best_error = backtests[best_method]

        # Only prefer a fancy method if it actually beats naive
        if best_method != "naive" and best_error >= naive_error:
            recommended = "naive"
            reason = "no method beat the naive baseline on backtest"
        else:
            recommended = best_method
            reason = f"lowest backtest error ({best_error:.1%})"
    else:
        recommended = "naive"
        reason = "series too short to backtest"

    return {
        "ticker": ticker,
        "metric": metric,
        "period_type": period_type,
        "unit": data["unit"],
        "history": values,
        "recommended_method": recommended,
        "recommended_reason": reason,
        "forecast": predictions[recommended],
        "all_predictions": predictions,
        "backtest_errors": backtests,
               "latest_actual": values[-1],
        "reliable": backtests.get(recommended, 1.0) < 0.15,
    }

# ── Demo ───────────────────────────────────────────────────

def fmt(n, unit):
    if n is None:
        return "n/a"
    if unit == "USD/shares":
        return f"${n:.2f}"
    if abs(n) >= 1e9:
        return f"${n/1e9:.2f}B"
    if abs(n) >= 1e6:
        return f"${n/1e6:.1f}M"
    return f"{n:,.0f}"


def main():
    tests = [
        ("MSFT", "revenue"),
        ("CRM", "revenue"),
        ("GOOGL", "net_income"),
    ]

    for ticker, metric in tests:
        print("=" * 60)
        print(f"  Forecast: {ticker} {metric} (next quarter)")
        print("=" * 60)

        result = forecast_metric(ticker, metric, horizon=1)
        if "error" in result:
            print(" ", result["error"])
            print()
            continue

        unit = result["unit"]
        print(f"  History: {' -> '.join(fmt(v, unit) for v in result['history'][-5:])}")
        print()
        print(f"  {'method':16} {'prediction':>14} {'backtest err':>14}")
        for name in METHODS:
            pred = fmt(result["all_predictions"][name], unit)
            err = result["backtest_errors"].get(name)
            err_str = f"{err:.1%}" if err is not None else "n/a"
            marker = "  <-- chosen" if name == result["recommended_method"] else ""
            print(f"  {name:16} {pred:>14} {err_str:>14}{marker}")
        print()
        print(f"  Recommended: {fmt(result['forecast'], unit)} "
              f"({result['recommended_method']} — {result['recommended_reason']})")
        print()


if __name__ == "__main__":
    main()