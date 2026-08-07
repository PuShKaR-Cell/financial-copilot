"""Step 34 — Full LangGraph orchestration graph.

planner -> [retrieval, numeric, sentiment] -> synthesis -> critic -> output
(with a bounded retry loop back to synthesis on failed verification)
"""
