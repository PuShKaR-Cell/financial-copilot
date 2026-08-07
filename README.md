# Financial Research Copilot

Multimodal, multi-agent research copilot over SEC filings, earnings
call audio, and structured financial data. See the build guide for
the full 58-step plan.

## Structure

- `ingestion/`  — EDGAR filings, XBRL facts, market/macro data, audio (Steps 6-10)
- `processing/` — PDF parsing, visual embeddings, tables, chunking, reranking (Steps 11-18)
- `agents/`     — planner, retrieval, numeric, sentiment, synthesis, critic (Steps 27-35)
- `eval/`       — golden dataset, metrics, CI eval runner (Steps 39-45)
- `api/`        — FastAPI backend (Step 36)
- `frontend/`   — Streamlit UI (Step 37)
- `infra/`      — docker-compose, deployment config (Step 3, Step 52)

## Status

Repo scaffolded (Step 2 done). Next: Step 3 — local infrastructure
(docker-compose up for Qdrant, Postgres, Langfuse).

## Setup

    cp .env.example .env   # fill in your keys
    python -m venv .venv && source .venv/bin/activate
    pip install -r requirements.txt
