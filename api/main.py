"""Step 36 — FastAPI backend.

Wraps the agent graph in an HTTP API.

Because a full query takes minutes on CPU, /query does not block. It
starts the work in a background thread, returns a job_id immediately,
and the client polls /query/{job_id} for progress and the final result.
This async job pattern is what the streaming UI (Step 38) builds on and
is how real long-running LLM workloads are served.

Endpoints:
  POST /query            -> {job_id}         start a question
  GET  /query/{job_id}   -> {status, ...}    poll for progress/result
  GET  /health           -> {status}         liveness + model check
  POST /feedback         -> {ok}             thumbs up/down on an answer
"""

import os
import sys
import uuid
import time
import threading
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import settings

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from agents import graph
from agents import citations
from agents import llm

app = FastAPI(title="Financial Research Copilot", version="1.0")

# Allow the local frontend (Step 37) to call this API from the browser
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # fine for local dev; tighten for deploy
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── In-memory job store ────────────────────────────────────
# A dict is fine for a single-process dev server. For real deployment
# this would move to Redis or a DB (noted for Phase 9).

JOBS = {}
JOBS_LOCK = threading.Lock()


class QueryRequest(BaseModel):
    question: str


class FeedbackRequest(BaseModel):
    job_id: str
    rating: str        # "up" | "down"
    note: str = ""


def _set_job(job_id, **fields):
    with JOBS_LOCK:
        JOBS.setdefault(job_id, {})
        JOBS[job_id].update(fields)


def _get_job(job_id):
    with JOBS_LOCK:
        return dict(JOBS.get(job_id, {})) or None


def _run_job(job_id, question):
    """Background worker: run the graph and store the result."""
    _set_job(job_id, status="running", stage="planning",
             started_at=datetime.utcnow().isoformat())
    try:
        state = graph.answer_question(question, verbose=False)

        refs = citations.resolve_references(state)
        report = citations.verify_citations(state)

        _set_job(
            job_id,
            status="done",
            stage="complete",
            answer=state.final_answer,
            verification_status=state.verification_status,
            sources=refs,
            citation_report=report,
            sub_tasks=[
                {"agent": t.agent.value, "description": t.description}
                for t in state.sub_tasks
            ],
            evidence_count=len(state.evidence),
            finished_at=datetime.utcnow().isoformat(),
        )
    except Exception as e:
        _set_job(job_id, status="error", stage="failed", error=str(e))


@app.get("/health")
def health():
    """Liveness plus a quick model reachability check."""
    ok, message = llm.health_check()
    return {
        "status": "ok" if ok else "degraded",
        "model": settings.ollama_model,
        "model_check": message,
        "time": datetime.utcnow().isoformat(),
    }


@app.post("/query")
def start_query(req: QueryRequest):
    """Start a question. Returns a job_id to poll."""
    question = (req.question or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="question is required")
    if len(question) > 500:
        raise HTTPException(status_code=400, detail="question too long (max 500 chars)")

    job_id = uuid.uuid4().hex[:12]
    _set_job(job_id, status="queued", stage="queued", question=question)

    thread = threading.Thread(target=_run_job, args=(job_id, question), daemon=True)
    thread.start()

    return {"job_id": job_id, "status": "queued"}


@app.get("/query/{job_id}")
def poll_query(job_id: str):
    """Poll a job for status and, once done, the answer."""
    job = _get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="unknown job_id")
    return job


@app.post("/feedback")
def feedback(req: FeedbackRequest):
    """Record thumbs up/down. Appended to a JSONL file for later analysis."""
    if req.rating not in ("up", "down"):
        raise HTTPException(status_code=400, detail="rating must be 'up' or 'down'")

    job = _get_job(req.job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="unknown job_id")

    import json
    os.makedirs("data", exist_ok=True)
    record = {
        "job_id": req.job_id,
        "question": job.get("question"),
        "rating": req.rating,
        "note": req.note,
        "time": datetime.utcnow().isoformat(),
    }
    with open(os.path.join("data", "feedback.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")

    return {"ok": True}


@app.get("/")
def root():
    return {
        "service": "Financial Research Copilot",
        "endpoints": ["/health", "POST /query", "GET /query/{job_id}", "POST /feedback"],
    }