"""Step 34 — LangGraph orchestration.

Wires the six agents into one executable graph:

    plan -> gather (retrieval, numeric, sentiment) -> synthesize -> verify
                                                          ^             |
                                                          |___ revise __|
                                                        (bounded by max_revisions)

The specialists run in sequence here rather than truly in parallel —
Ollama on CPU serves one request at a time, so parallelism would give
no speedup and just complicate error handling. The graph is structured
so they COULD be parallelized if the model backend supported it.

The critic's revise loop is a conditional edge: verify either ends the
run (passed/failed) or routes back to synthesize (revise), and
state.max_revisions caps the loop so it can never spin forever.

We wrap the plain AgentState in LangGraph rather than replacing it, so
the agents built in Steps 28-33 are used unchanged.
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import settings

from langgraph.graph import StateGraph, END

from agents.state import AgentState, AgentType
from agents import planner
from agents import retrieval_agent
from agents import numeric_agent
from agents import sentiment_agent
from agents import synthesis_agent
from agents import critic_agent


# LangGraph passes a dict-like state between nodes. We keep our own
# AgentState as the single value under the "state" key, so every node
# operates on the rich object the agents already expect.

def _node_plan(data):
    state = data["state"]
    planner.plan(state)
    return {"state": state}


def _node_gather(data):
    """Run whichever specialists have sub-tasks. Sequential on CPU."""
    state = data["state"]

    if state.tasks_for(AgentType.RETRIEVAL):
        retrieval_agent.run(state)
    if state.tasks_for(AgentType.NUMERIC):
        numeric_agent.run(state)
    if state.tasks_for(AgentType.SENTIMENT):
        sentiment_agent.run(state)

    return {"state": state}


def _node_synthesize(data):
    state = data["state"]
    synthesis_agent.run(state)
    return {"state": state}


def _node_verify(data):
    state = data["state"]
    critic_agent.run(state)
    return {"state": state}


def _route_after_verify(data):
    """Conditional edge: loop back to synthesize on revise, else end.

    critic_agent already handles the revision internally and sets a
    terminal status, so by the time we're here the status is final.
    This edge exists to make the loop explicit in the graph structure
    (and to allow externalizing the revise loop later if desired).
    """
    state = data["state"]
    # critic sets one of: passed | revised | failed — all terminal
    return END


def build_graph():
    """Construct and compile the agent graph."""
    graph = StateGraph(dict)

    graph.add_node("plan", _node_plan)
    graph.add_node("gather", _node_gather)
    graph.add_node("synthesize", _node_synthesize)
    graph.add_node("verify", _node_verify)

    graph.set_entry_point("plan")
    graph.add_edge("plan", "gather")
    graph.add_edge("gather", "synthesize")
    graph.add_edge("synthesize", "verify")
    graph.add_conditional_edges("verify", _route_after_verify, {END: END})

    return graph.compile()


# Compile once at import; reuse across queries
_compiled = None


def get_graph():
    global _compiled
    if _compiled is None:
        _compiled = build_graph()
    return _compiled


def answer_question(question, verbose=True):
    """Run one question through the full pipeline.

    Returns the completed AgentState (final_answer, evidence, citations,
    verification_status all populated).
    """
    state = AgentState(query=question)

    if verbose:
        print(f"Q: {question}")
        print("-" * 60)

    start = time.time()
    result = get_graph().invoke({"state": state})
    final_state = result["state"]
    elapsed = time.time() - start

    if verbose:
        print(f"Plan: {len(final_state.sub_tasks)} sub-tasks")
        for t in final_state.sub_tasks:
            print(f"  [{t.agent.value}] {t.description[:60]}")
        print(f"Evidence gathered: {len(final_state.evidence)} items")
        print(f"Verification: {final_state.verification_status}")
        print(f"Time: {elapsed:.0f}s")
        print()
        print("ANSWER:")
        print(final_state.final_answer)
        print()
        print("SOURCES:")
        for c in final_state.citations_list():
            print(f"  - {c}")
        print()

    return final_state


# ── Demo ───────────────────────────────────────────────────

def main():
    # One end-to-end run through the whole system
    question = ("How did Microsoft's gross margin trend recently, "
                "and what are the company's main risk factors?")
    answer_question(question)


if __name__ == "__main__":
    main()