"""Step 30 — Numeric agent.

Answers numeric sub-tasks by selecting the right Phase 4 tool and
letting that tool produce the number. The LLM decides WHICH tool and
WITH WHAT arguments; it never computes the figure itself — the
validated Python in numeric_tools does that.

Tool selection:
  lookup   — "what was revenue"          -> historical values
  ratio    — "what was gross margin"     -> derived ratio (margins)
  growth   — "how fast did X grow"       -> period-over-period growth
  forecast — "what will X be next quarter" -> projection + reliability

Margins are the case that needs judgment: "gross margin" is not a stored
metric, it's gross_profit / revenue. The agent recognizes margin language
and routes to the ratio tool with the correct numerator and denominator.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import settings

from agents import llm
from agents.state import AgentState, AgentType, Citation
from agents import numeric_tools


# Known margin phrasings -> (numerator, denominator)
MARGIN_MAP = {
    "gross margin": ("gross_profit", "revenue"),
    "gross_margin": ("gross_profit", "revenue"),
    "operating margin": ("operating_income", "revenue"),
    "operating_margin": ("operating_income", "revenue"),
    "net margin": ("net_income", "revenue"),
    "net_margin": ("net_income", "revenue"),
    "profit margin": ("net_income", "revenue"),
}


SELECT_SYSTEM = (
    "You choose which financial tool answers a sub-task. You return only a "
    "tool choice and its arguments as JSON. You do not compute anything."
)

SELECT_PROMPT = """Choose the tool to answer this sub-task.

SUB-TASK: {task}
COMPANY TICKER: {ticker}
SUGGESTED METRIC: {metric}

TOOLS:
  lookup   — historical values of a metric. args: metric
  ratio    — a margin or ratio. args: numerator, denominator
  growth   — growth rate of a metric. args: metric
  forecast — next-quarter projection. args: metric

KNOWN METRICS: {metrics}

Guidance:
- "margin" questions use ratio (gross margin = gross_profit / revenue).
- "grow/growth/increase" questions use growth.
- "forecast/project/next quarter/expect" questions use forecast.
- plain "what was/is X" questions use lookup.

Return JSON:
  {{"tool": "lookup|ratio|growth|forecast",
    "metric": "metric name (for lookup/growth/forecast) or null",
    "numerator": "metric (for ratio) or null",
    "denominator": "metric (for ratio) or null"}}"""


def _detect_margin(text):
    t = text.lower()
    for phrase, (num, den) in MARGIN_MAP.items():
        if phrase in t:
            return num, den
    return None


def _choose_tool(task):
    """Decide which tool + args to use for a sub-task.

    Tries a cheap rule first (margin phrases), falls back to the LLM.
    """
    # Fast path: obvious margin language, no LLM needed
    margin = _detect_margin(task.description)
    if margin:
        return {"tool": "ratio", "numerator": margin[0], "denominator": margin[1]}

    prompt = SELECT_PROMPT.format(
        task=task.description,
        ticker=task.ticker or "unknown",
        metric=task.metric or "none",
        metrics=", ".join(numeric_tools.available_metrics()),
    )
    choice = llm.complete_json(prompt, system=SELECT_SYSTEM, max_tokens=200)

    if not isinstance(choice, dict) or "tool" not in choice:
        # Fallback: lookup with the planner's suggested metric
        return {"tool": "lookup", "metric": task.metric or "revenue"}
    return choice


def _citation_from_source(src):
    """Turn a tool's source dict into a Citation."""
    if not src:
        return Citation(source_type="xbrl")
    stype = src.get("type", "xbrl")
    if stype == "forecast":
        return Citation(source_type="forecast", ticker=src.get("ticker"),
                        detail=src.get("method"))
    if stype == "filing_table":
        return Citation(source_type="filing_table", ticker=src.get("ticker"),
                        filing=src.get("filing"), table_index=src.get("table_index"))
    # xbrl and xbrl_derived
    return Citation(source_type="xbrl", ticker=src.get("ticker"),
                    period=src.get("period"), detail=src.get("metric"))


def run(state: AgentState):
    """Execute all numeric sub-tasks, writing evidence into state."""
    tasks = state.tasks_for(AgentType.NUMERIC)

    for task in tasks:
        if not task.ticker:
            state.add_evidence(
                AgentType.NUMERIC,
                f"Cannot compute — no company identified for: {task.description}",
                Citation(source_type="xbrl"),
                confidence="low",
            )
            task.done = True
            continue

        choice = _choose_tool(task)
        tool = choice.get("tool", "lookup")

        try:
            if tool == "ratio":
                num = choice.get("numerator") or "gross_profit"
                den = choice.get("denominator") or "revenue"
                result = numeric_tools.ratio(task.ticker, num, den)
            elif tool == "growth":
                metric = choice.get("metric") or task.metric or "revenue"
                result = numeric_tools.growth(task.ticker, metric)
            elif tool == "forecast":
                metric = choice.get("metric") or task.metric or "revenue"
                result = numeric_tools.forecast(task.ticker, metric)
            else:  # lookup
                metric = choice.get("metric") or task.metric or "revenue"
                result = numeric_tools.lookup(task.ticker, metric)
        except Exception as e:
            state.add_evidence(
                AgentType.NUMERIC,
                f"Numeric tool error for '{task.description}': {e}",
                Citation(source_type="xbrl", ticker=task.ticker),
                confidence="low",
            )
            task.done = True
            continue

        # Determine confidence: forecasts carry their own reliability
        confidence = "high"
        if result["tool"] == "forecast":
            src = result.get("source") or {}
            confidence = "medium" if src.get("reliable") else "low"
        elif "error" in result.get("data", {}):
            confidence = "low"

        state.add_evidence(
            AgentType.NUMERIC,
            result["summary"],
            _citation_from_source(result.get("source")),
            confidence=confidence,
            raw=result.get("data"),
        )
        task.done = True

    return state


# ── Demo ───────────────────────────────────────────────────

def main():
    state = AgentState(query="test")
    state.add_sub_task("Get Microsoft's gross margin trend",
                       AgentType.NUMERIC, ticker="MSFT", metric="gross_profit")
    state.add_sub_task("How fast did Salesforce revenue grow",
                       AgentType.NUMERIC, ticker="CRM", metric="revenue")
    state.add_sub_task("Forecast Microsoft's revenue next quarter",
                       AgentType.NUMERIC, ticker="MSFT", metric="revenue")
    state.add_sub_task("Forecast Google's net income next quarter",
                       AgentType.NUMERIC, ticker="GOOGL", metric="net_income")

    print("Running numeric agent on 4 sub-tasks...")
    print()

    run(state)

    for e in state.evidence_from(AgentType.NUMERIC):
        print(f"  [{e.confidence:6}] {e.content}")
        print(f"           source: {e.citation.render()}")
        print()


if __name__ == "__main__":
    main()