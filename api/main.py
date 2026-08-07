"""Step 36 — FastAPI backend: /query, /health, /feedback."""

from fastapi import FastAPI

app = FastAPI(title="Financial Research Copilot")


@app.get("/health")
def health():
    return {"status": "ok"}
