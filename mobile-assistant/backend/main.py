"""FastAPI backend for the mobile AI assistant."""

from __future__ import annotations

from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

load_dotenv()

import assistant
import tools as tool_funcs
from db import init_db

app = FastAPI(title="Mobile AI Assistant")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _startup() -> None:
    init_db()


class ChatRequest(BaseModel):
    message: str
    timezone: str = "UTC"
    user_id: str = "default"


class ChatResponse(BaseModel):
    reply: str


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    history = assistant.load_history(req.user_id)
    try:
        reply, updated = assistant.run_turn(history, req.message, req.timezone)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"assistant error: {exc}") from exc
    assistant.save_history(req.user_id, updated)
    return ChatResponse(reply=reply or "(no response)")


@app.post("/chat/reset")
def chat_reset(user_id: str = "default") -> dict[str, str]:
    assistant.reset_history(user_id)
    return {"status": "reset"}


@app.get("/memories")
def get_memories(query: str = "", limit: int = 50) -> list[dict[str, Any]]:
    import json

    raw = tool_funcs.list_memories(query, limit)
    if raw == "No memories found.":
        return []
    return json.loads(raw)


@app.get("/reminders")
def get_reminders(include_done: bool = False) -> list[dict[str, Any]]:
    import json

    raw = tool_funcs.list_reminders(include_done)
    if raw == "No reminders.":
        return []
    return json.loads(raw)


class MemoryDeleteResponse(BaseModel):
    status: str


@app.delete("/memories/{memory_id}", response_model=MemoryDeleteResponse)
def delete_memory(memory_id: int) -> MemoryDeleteResponse:
    result = tool_funcs.delete_memory(memory_id)
    if result.startswith("No memory"):
        raise HTTPException(status_code=404, detail=result)
    return MemoryDeleteResponse(status=result)


@app.post("/reminders/{reminder_id}/complete", response_model=MemoryDeleteResponse)
def complete_reminder(reminder_id: int) -> MemoryDeleteResponse:
    result = tool_funcs.complete_reminder(reminder_id)
    if result.startswith("No reminder"):
        raise HTTPException(status_code=404, detail=result)
    return MemoryDeleteResponse(status=result)
