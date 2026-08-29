"""Step 28 — Planner / router agent.

Turns a natural-language question into structured sub-tasks, each
routed to the specialist best equipped to answer it:

    numeric    — anything about figures: revenue, margins, growth, forecasts
    retrieval  — anything about what filings *say*: risks, strategy, commentary
    sentiment  — anything about tone: how management/the Fed sounded

The planner also resolves entities up front — mapping "Microsoft" to
MSFT and "profitability" to a known metric — so the specialists receive
clean, unambiguous tasks rather than re-parsing the question three times.

A question can fan out to several specialists. "How did margins trend and
did management sound confident?" produces a numeric task AND a sentiment
task, which is what makes the final answer genuinely multi-source.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import settings

from agents import llm
from agents.state import AgentState, AgentType
from agents import numeric_tools


# Company universe — the planner maps names/aliases onto tickers.
# Loaded once from the same source of truth as the rest of the system.
def _load_companies():
    import yaml
    with open("companies.yaml") as f:
        data = yaml.safe_load(f)
    mapping = {}
    for c in data["companies"]:
        ticker = c["ticker"]
        mapping[ticker.lower()] = ticker
        mapping[c["name"].lower()] = ticker
    return mapping


COMPANY_MAP = _load_companies()
KNOWN_METRICS = numeric_tools.available_metrics()


PLANNER_SYSTEM = (
    "You are the planning component of a financial research assistant. "
    "You break a user's question into concrete sub-tasks and route each "
    "to the right specialist. You do not answer the question yourself."
)

PLANNER_PROMPT = """Break this question into sub-tasks for specialist agents.

QUESTION: {question}

SPECIALISTS:
  numeric   — financial figures: revenue, margins, growth, forecasts, any number
  retrieval — what filings say: risks, strategy, business commentary, explanations
  sentiment — tone and confidence in earnings calls / Fed press conferences

KNOWN COMPANIES (name -> ticker): {companies}
KNOWN METRICS (for numeric tasks): {metrics}

Rules:
- Produce one sub-task per distinct thing the question asks.
- A question may need multiple specialists. Split accordingly.
- For numeric tasks, set "metric" to the closest known metric name.
- Set "ticker" to the company's ticker when a company is mentioned.
- If the question mentions no company and needs one, leave ticker null.

Return JSON: a list of sub-task objects, each with:
  "description": what this sub-task should find (one sentence)
  "agent": "numeric" | "retrieval" | "sentiment"
  "ticker": ticker symbol or null
  "metric": known metric name or null

Example question: "How did Salesforce revenue grow, and did management sound confident?"
Example output:
[
  {{"description": "Get Salesforce revenue growth over recent quarters", "agent": "numeric", "ticker": "CRM", "metric": "revenue"}},
  {{"description": "Assess management's tone and confidence", "agent": "sentiment", "ticker": "CRM", "metric": null}}
]"""


def _resolve_ticker(raw):
    """Map a model-provided ticker/name to a known ticker, or None."""
    if not raw:
        return None
    key = str(raw).lower().strip()
    if key in COMPANY_MAP:
        return COMPANY_MAP[key]
    # The model sometimes returns a ticker that's already valid
    upper = str(raw).upper().strip()
    if upper in COMPANY_MAP.values():
        return upper
    return None


def _resolve_metric(raw):
    """Snap a model-provided metric onto a known metric name, or None."""
    if not raw:
        return None
    key = str(raw).lower().strip().replace(" ", "_")
    if key in KNOWN_METRICS:
        return key
    # loose contains-match: "gross margin" -> "gross_profit"
    for m in KNOWN_METRICS:
        if key in m or m in key:
            return m
    return None


def _valid_agent(raw):
    try:
        return AgentType(str(raw).lower().strip())
    except ValueError:
        return None


def plan(state: AgentState):
    """Populate state.sub_tasks from the user's query."""
    prompt = PLANNER_PROMPT.format(
        question=state.query,
        companies=", ".join(f"{k}->{v}" for k, v in COMPANY_MAP.items()
                            if k != v.lower())[:900],
        metrics=", ".join(KNOWN_METRICS),
    )

    result = llm.complete_json(prompt, system=PLANNER_SYSTEM, max_tokens=700)

    # Defensive: the model might return a bare object or wrap the list
    if isinstance(result, dict):
        # sometimes returns {"sub_tasks": [...]} or {"tasks": [...]}
        for key in ("sub_tasks", "tasks", "subtasks"):
            if key in result and isinstance(result[key], list):
                result = result[key]
                break
        else:
            result = [result]

    if not isinstance(result, list):
        state.errors.append("planner: could not parse sub-tasks")
        # Fallback: one retrieval task with the raw query
        state.add_sub_task(state.query, AgentType.RETRIEVAL)
        return state

    for item in result:
        if not isinstance(item, dict):
            continue
        agent = _valid_agent(item.get("agent"))
        if agent is None:
            continue
        state.add_sub_task(
            description=item.get("description", state.query),
            agent=agent,
            ticker=_resolve_ticker(item.get("ticker")),
            metric=_resolve_metric(item.get("metric")),
        )

    # Never leave the pipeline with nothing to do
    if not state.sub_tasks:
        state.errors.append("planner: no valid sub-tasks; defaulting to retrieval")
        state.add_sub_task(state.query, AgentType.RETRIEVAL)

    return state


# ── Demo ───────────────────────────────────────────────────

def main():
    questions = [
        "How did Microsoft's gross margin trend over the last few quarters?",
        "How did Salesforce revenue grow, and did management sound confident about it?",
        "What are Snowflake's biggest risk factors, and what's their net income forecast?",
        "What did the Fed say about interest rates and how did they sound?",
    ]

    for q in questions:
        print("=" * 64)
        print("Q:", q)
        print("=" * 64)
        state = AgentState(query=q)
        plan(state)
        for t in state.sub_tasks:
            tk = t.ticker or "-"
            mt = t.metric or "-"
            print(f"  [{t.agent.value:9}] ticker={tk:6} metric={mt:16} {t.description}")
        if state.errors:
            print("  errors:", state.errors)
        print()


if __name__ == "__main__":
    main()
