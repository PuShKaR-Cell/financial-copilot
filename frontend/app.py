"""Step 37 — Streamlit frontend.

A thin UI over the FastAPI backend. It does no reasoning itself — it
starts a query, polls the job, and renders the result: the answer, the
numbered sources, the plan the system chose, and the verification status.

Because a query takes many minutes, the UI polls /query/{job_id} on a
loop and shows which stage the pipeline is in, so the wait is legible
rather than a frozen spinner. This is the screen shown in the demo.

Run with:   streamlit run frontend/app.py
(the API must be running separately on port 8000)
"""

import time
import requests
import streamlit as st

API = "http://localhost:8000"

# Example companies for the picker — mirrors companies.yaml
COMPANIES = [
    "MSFT", "GOOGL", "AMZN", "CRM", "SNOW", "DDOG", "NET", "MDB",
    "ZS", "PLTR", "TEAM", "HUBS", "PANW", "WDAY", "NOW",
]

EXAMPLES = [
    "How did Microsoft's gross margin trend recently?",
    "What are Snowflake's biggest risk factors?",
    "How did Salesforce revenue grow, and did the Fed sound confident about rates?",
    "What did the Fed say about inflation and how did they sound?",
]

STAGE_LABELS = {
    "queued": "Queued...",
    "planning": "🧭 Planning — breaking the question into sub-tasks",
    "retrieval: searching filings": "📄 Retrieval agent — searching SEC filings",
    "numeric: computing figures": "🔢 Numeric agent — computing financial figures",
    "sentiment: analyzing transcripts": "🎙️ Sentiment agent — analyzing Fed transcripts",
    "synthesizing answer": "✍️ Synthesis agent — composing the answer",
    "verifying claims": "🔍 Critic agent — verifying every claim against sources",
    "complete": "Complete",
    "failed": "Failed",
}


st.set_page_config(page_title="Financial Research Copilot", page_icon="📊",
                   layout="centered")

st.title("📊 Financial Research Copilot")
st.caption("Multi-agent research over SEC filings, financial data, and Fed transcripts — "
           "every claim verified against its source.")

# ── API health indicator ───────────────────────────────────
try:
    h = requests.get(f"{API}/health", timeout=5).json()
    if h.get("status") == "ok":
        st.success(f"Connected · model: {h.get('model')}", icon="✅")
    else:
        st.warning(f"API degraded: {h.get('model_check')}", icon="⚠️")
except Exception:
    st.error("Cannot reach the API. Start it with: uvicorn api.main:app --port 8000",
             icon="🚫")
    st.stop()

# ── Query input ────────────────────────────────────────────
st.subheader("Ask a question")

with st.expander("Example questions"):
    for ex in EXAMPLES:
        if st.button(ex, key=f"ex_{ex}"):
            st.session_state["question"] = ex

question = st.text_area(
    "Your question",
    value=st.session_state.get("question", ""),
    height=90,
    placeholder="e.g. How did Microsoft's gross margin trend, and why?",
)

col1, col2 = st.columns([1, 3])
with col1:
    company_hint = st.selectbox("Company (optional)", ["Any"] + COMPANIES)
with col2:
    st.write("")
    st.write("")
    ask = st.button("Ask", type="primary", use_container_width=True)


def poll_until_done(job_id, placeholder):
    """Poll the job, updating a status line, until it finishes."""
    start = time.time()
    while True:
        try:
            job = requests.get(f"{API}/query/{job_id}", timeout=10).json()
        except Exception as e:
            placeholder.error(f"Lost connection to API: {e}")
            return None

        status = job.get("status")
        stage = job.get("stage", status)
        elapsed = int(time.time() - start)

        if status in ("done", "error"):
            return job

        label = STAGE_LABELS.get(stage, stage)
        placeholder.info(f"{label}  ·  {elapsed // 60}m {elapsed % 60}s elapsed")
        time.sleep(5)


if ask and question.strip():
    # Prepend the company hint if one was chosen and not already named
    q = question.strip()
    if company_hint != "Any" and company_hint.lower() not in q.lower():
        q = f"[{company_hint}] {q}"

    try:
        resp = requests.post(f"{API}/query", json={"question": q}, timeout=10).json()
    except Exception as e:
        st.error(f"Could not start query: {e}")
        st.stop()

    job_id = resp.get("job_id")
    if not job_id:
        st.error(f"Unexpected response: {resp}")
        st.stop()

    st.info("This runs a full multi-agent pipeline on CPU — expect several minutes.")
    status_box = st.empty()
    job = poll_until_done(job_id, status_box)

    if job is None:
        st.stop()
    if job.get("status") == "error":
        status_box.error(f"Query failed: {job.get('error')}")
        st.stop()

    status_box.success("Done", icon="✅")

    # ── Answer ─────────────────────────────────────────────
    st.subheader("Answer")

    verdict = job.get("verification_status")
    if verdict == "passed":
        st.caption("✅ All factual claims verified against sources")
    elif verdict == "revised":
        st.caption("✏️ Revised to remove unsupported claims")
    elif verdict == "failed":
        st.caption("⚠️ Some claims could not be verified — showing only what was confirmed")

    st.write(job.get("answer", "(no answer)"))

    # ── Sources ────────────────────────────────────────────
    sources = job.get("sources", [])
    if sources:
        st.subheader("Sources")
        for s in sources:
            with st.container():
                st.markdown(f"**[{s['ref']}]** {s['citation']}  "
                            f"· _{s['source_type']}, {s['confidence']} confidence_")
                st.caption(s["content"])

    # ── How it was answered ────────────────────────────────
    with st.expander("How this was answered"):
        st.write(f"**Evidence gathered:** {job.get('evidence_count', 0)} items")
        st.write("**Sub-tasks the planner created:**")
        for t in job.get("sub_tasks", []):
            st.write(f"- `{t['agent']}` — {t['description']}")
        report = job.get("citation_report", {})
        if report:
            st.write(f"**Citation check:** {report.get('valid_refs', 0)} valid references, "
                     f"{len(report.get('dangling_refs', []))} dangling")

    # ── Feedback ───────────────────────────────────────────
    st.subheader("Was this helpful?")
    fb1, fb2, _ = st.columns([1, 1, 4])
    with fb1:
        if st.button("👍 Yes"):
            requests.post(f"{API}/feedback",
                          json={"job_id": job_id, "rating": "up"})
            st.success("Thanks!")
    with fb2:
        if st.button("👎 No"):
            requests.post(f"{API}/feedback",
                          json={"job_id": job_id, "rating": "down"})
            st.success("Thanks — noted.")