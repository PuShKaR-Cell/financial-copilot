"""Step 24 — Table QA over extracted financial tables.

Answers numeric questions by finding the relevant table and having
the LLM read it, then validates the answer against XBRL ground truth.

Pipeline:
  1. Semantic search over table_chunks to find candidate tables
  2. Fetch the full table text from Postgres (Qdrant only stores a preview)
  3. LLM extracts the requested figure, returning JSON with the value,
     the period, and the exact row/column it came from
  4. Where the metric exists in financial_facts, compare and report
     whether the extracted figure matches within tolerance

Step 4 is the point of this module. Anyone can prompt an LLM to read a
table; knowing whether it read it *correctly* requires a ground truth
to check against — which is what the XBRL facts from Step 23 provide.
"""

import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import settings

import psycopg2
from sentence_transformers import SentenceTransformer
from qdrant_client import QdrantClient

from agents import llm
from agents import financial_tools

COLLECTION = "table_chunks"
TEXT_MODEL = "all-MiniLM-L6-v2"
MAX_TABLE_CHARS = 3500     # keep prompts within the model's context
TOLERANCE = 0.01           # 1% — allows for rounding/units differences

_encoder = None


def get_encoder():
    global _encoder
    if _encoder is None:
        _encoder = SentenceTransformer(TEXT_MODEL)
    return _encoder


def get_db():
    return psycopg2.connect(settings.postgres_url)


def find_tables(question, ticker=None, limit=5):
    """Semantic search for tables relevant to the question.

    The question is expanded with income-statement vocabulary because
    a bare question ("what was revenue") embeds poorly against table
    text, which reads like "Revenue | Cost of revenue | Gross margin".
    """
    client = QdrantClient(url=settings.qdrant_url)
    expanded = question + " revenue cost of revenue gross margin operating income net income total"
    vector = get_encoder().encode(expanded).tolist()

    query_filter = None
    if ticker:
        query_filter = {"must": [{"key": "ticker", "match": {"value": ticker.upper()}}]}

    response = client.query_points(
        collection_name=COLLECTION,
        query=vector,
        limit=limit,
        query_filter=query_filter,
        with_payload=True,
    )

    return [
        {
            "score": p.score,
            "ticker": p.payload.get("ticker"),
            "filing": p.payload.get("filing"),
            "table_index": p.payload.get("table_index"),
        }
        for p in response.points
    ]


def fetch_table_text(ticker, filing, table_index):
    """Get the full serialized table from Postgres.

    Qdrant stores only a 300-char preview; the full text lives in
    the table_data table written during Step 14.
    """
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT table_text, num_rows, num_cols
        FROM table_data
        WHERE ticker = %s AND filing = %s AND table_index = %s
    """, (ticker, filing, table_index))
    row = cur.fetchone()
    cur.close()
    conn.close()

    if not row:
        return None
    return {"text": row[0], "num_rows": row[1], "num_cols": row[2]}


EXTRACT_SYSTEM = (
    "You read financial tables from SEC filings and extract specific figures. "
    "You report only what the table actually contains. If the table does not "
    "answer the question, you say so rather than guessing."
)

EXTRACT_PROMPT = """Answer the question using ONLY the table below.

TABLE (from {ticker}, filing {filing}):
{table}

QUESTION: {question}

IMPORTANT — units: financial tables state their scale in a header row,
usually "(In millions)" or "(In thousands, except per share data)" near
the top of the table. Find that header and report the matching unit. If
the table says "In millions", the unit is USD_millions even though the
cells show plain numbers like 5,816. Do not assume plain USD.

Return JSON with these keys:
  "found": true if the table answers the question, false otherwise
  "value": the numeric value as a plain number (no $ or commas), or null
  "unit": "USD", "USD_millions", "USD_thousands", "percent", or "shares"
  "period": the column header or period label the value came from, or null
  "row_label": the exact row label the value came from, or null
  "note": one short sentence explaining the answer, or why it wasn't found

If the table does not contain the answer, set found to false and value to null."""


def ask_table(question, table_text, ticker, filing):
    """Have the LLM extract a figure from one table."""
    prompt = EXTRACT_PROMPT.format(
        ticker=ticker,
        filing=filing,
        table=table_text[:MAX_TABLE_CHARS],
        question=question,
    )
    result = llm.complete_json(prompt, system=EXTRACT_SYSTEM, max_tokens=400)

    if result is None:
        return {"found": False, "value": None, "note": "LLM returned unparseable output"}
    return result


def normalize_value(value, unit):
    """Convert a reported figure to plain USD for comparison.

    Filings report in millions or thousands; XBRL reports absolute
    dollars. Without this, every comparison fails by 6 orders of magnitude.
    """
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None

    if unit == "USD_millions":
        return v * 1e6
    if unit == "USD_thousands":
        return v * 1e3
    return v


def validate_against_xbrl(ticker, metric, extracted_value, unit, periods=8):
    """Check an extracted figure against XBRL ground truth.

    Looks for the value in any of the recent periods for that metric,
    across all period types (the table might be quarterly or annual).
    Returns the closest match and whether it's within tolerance.
    """
    normalized = normalize_value(extracted_value, unit)
    if normalized is None:
        return {"checked": False, "reason": "no numeric value extracted"}

    candidates = []
    for ptype in ("quarterly", "ytd", "annual", "instant"):
        result = financial_tools.get_metric(
            ticker, metric, periods=periods, period_type=ptype
        )
        if "error" in result:
            continue
        for v in result["values"]:
            if v["value"] is not None:
                candidates.append((v["period_end"], ptype, v["value"]))

    if not candidates:
        return {"checked": False, "reason": "no XBRL data for " + metric + " on " + ticker}

    # Find the closest ground-truth value by relative difference
    best = min(
        candidates,
        key=lambda c: abs(c[2] - normalized) / max(abs(c[2]), 1),
    )
    period_end, ptype, truth = best
    rel_diff = abs(truth - normalized) / max(abs(truth), 1)

    return {
        "checked": True,
        "match": rel_diff <= TOLERANCE,
        "extracted": normalized,
        "ground_truth": truth,
        "ground_truth_period": period_end,
        "ground_truth_type": ptype,
        "relative_diff": round(rel_diff, 4),
    }


def answer(question, ticker=None, validate_metric=None, verbose=True):
    """Full Table QA pipeline for one question."""
    if verbose:
        print("Q: " + question)
        if ticker:
            print("   (restricted to " + ticker + ")")
        print()

    tables = find_tables(question, ticker=ticker, limit=5)
    if not tables:
        return {"error": "no tables found"}

    for i, t in enumerate(tables, 1):
        table = fetch_table_text(t["ticker"], t["filing"], t["table_index"])
        if not table:
            continue

        if verbose:
            print("  Table " + str(i) + ": " + str(t["ticker"]) + " / "
                  + str(t["filing"])[:38] + "... "
                  + "(idx " + str(t["table_index"]) + ", score "
                  + format(t["score"], ".3f") + ")")

        result = ask_table(question, table["text"], t["ticker"], t["filing"])

        if result.get("found") and result.get("value") is not None:
            out = {
                "question": question,
                "answer": result,
                "source": {
                    "ticker": t["ticker"],
                    "filing": t["filing"],
                    "table_index": t["table_index"],
                },
            }

            if validate_metric:
                out["validation"] = validate_against_xbrl(
                    t["ticker"], validate_metric,
                    result["value"], result.get("unit", "USD"),
                )

            if verbose:
                print()
                print("  Value:  " + str(result["value"]) + " (" + str(result.get("unit")) + ")")
                print("  Period: " + str(result.get("period")))
                print("  Row:    " + str(result.get("row_label")))
                print("  Note:   " + str(result.get("note")))
                v = out.get("validation")
                if v and v.get("checked"):
                    mark = "MATCH" if v["match"] else "MISMATCH"
                    print()
                    print("  [" + mark + "] extracted "
                          + format(v["extracted"], ",.0f") + " vs XBRL "
                          + format(v["ground_truth"], ",.0f") + " ("
                          + v["ground_truth_type"] + ", " + v["ground_truth_period"]
                          + ") — " + format(v["relative_diff"], ".2%") + " diff")
                elif v:
                    print("  [not validated] " + str(v.get("reason")))

            return out

        if verbose:
            print("     -> not answerable from this table")

    return {"error": "none of the candidate tables answered the question",
            "tables_tried": len(tables)}


# ── Demo ───────────────────────────────────────────────────

def main():
    tests = [
        ("What was total revenue for the quarter?", "MSFT", "revenue"),
        ("What was net income?", "GOOGL", "net_income"),
        ("What were total operating expenses?", "CRM", "operating_expenses"),
    ]

    for question, ticker, metric in tests:
        print("=" * 62)
        answer(question, ticker=ticker, validate_metric=metric)
        print()


if __name__ == "__main__":
    main()