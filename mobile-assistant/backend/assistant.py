"""Claude-driven personal assistant. Uses the tool runner + web_search server tool."""

from __future__ import annotations

import json
from typing import Any

import anthropic
from anthropic import beta_tool

import tools as tool_funcs

MODEL = "claude-opus-5"

SYSTEM_PROMPT = """You are the user's personal AI assistant, running on their phone.

You help with day-to-day life: answering questions, searching for information,
remembering things they tell you, setting reminders, and making useful
suggestions when patterns emerge.

Guidelines:
- Be concise. Phone screens are small — no walls of text.
- When the user asks you to remember something, call save_memory.
- When the user mentions a fact worth remembering later (a preference, a name,
  a relationship, a recurring context), proactively save_memory it — but
  mention briefly that you did.
- Before answering questions that depend on remembered context, call
  list_memories to check.
- Before creating a reminder, call current_time with the user's timezone to
  resolve "tomorrow", "in an hour", "next Monday" to a concrete ISO timestamp.
- If asked to search or fetch current information (news, prices, weather,
  events), use web_search.
- Suggest things when it clearly helps — surface a relevant memory, propose a
  reminder, note a pattern — but don't be pushy or invent needs.
- If a request is destructive (delete a memory, cancel a reminder), confirm
  the specific item first."""


# --- Client-side tools (defined here, executed on the backend) ---

@beta_tool
def save_memory(content: str, tags: str = "") -> str:
    """Save something the user wants remembered for later.

    Args:
        content: What to remember, in the user's own words when possible.
        tags: Comma-separated tags for retrieval (e.g. "food,preference,restaurant").
    """
    return tool_funcs.save_memory(content, tags)


@beta_tool
def list_memories(query: str = "", limit: int = 20) -> str:
    """List saved memories. Call this before answering questions that depend on
    prior context.

    Args:
        query: Optional substring to filter by content or tags. Empty returns most recent.
        limit: Maximum number to return (default 20).
    """
    return tool_funcs.list_memories(query, limit)


@beta_tool
def delete_memory(memory_id: int) -> str:
    """Delete a memory by its id. Confirm with the user first.

    Args:
        memory_id: The memory's numeric id from list_memories.
    """
    return tool_funcs.delete_memory(memory_id)


@beta_tool
def create_reminder(text: str, remind_at_iso: str) -> str:
    """Create a reminder. Always call current_time first to resolve relative times.

    Args:
        text: What to remind the user about.
        remind_at_iso: ISO-8601 timestamp with timezone, e.g. 2026-08-11T14:30:00-04:00.
    """
    return tool_funcs.create_reminder(text, remind_at_iso)


@beta_tool
def list_reminders(include_done: bool = False) -> str:
    """List reminders.

    Args:
        include_done: Whether to include completed reminders.
    """
    return tool_funcs.list_reminders(include_done)


@beta_tool
def complete_reminder(reminder_id: int) -> str:
    """Mark a reminder as done.

    Args:
        reminder_id: The reminder's numeric id.
    """
    return tool_funcs.complete_reminder(reminder_id)


@beta_tool
def current_time(timezone_name: str = "UTC") -> str:
    """Return the current time in the given IANA timezone. Call this before
    creating a reminder so relative times ('in an hour', 'tomorrow at 9am')
    resolve correctly.

    Args:
        timezone_name: IANA timezone (e.g. America/New_York, Europe/London).
    """
    return tool_funcs.current_time(timezone_name)


CLIENT_TOOLS = [
    save_memory,
    list_memories,
    delete_memory,
    create_reminder,
    list_reminders,
    complete_reminder,
    current_time,
]

# Anthropic-hosted web search — runs server-side, no client execution.
SERVER_TOOLS: list[dict[str, Any]] = [
    {"type": "web_search_20260209", "name": "web_search"},
]


def run_turn(
    history: list[dict[str, Any]],
    user_message: str,
    user_timezone: str = "UTC",
) -> tuple[str, list[dict[str, Any]]]:
    """Run one conversation turn. Returns (assistant_text, updated_history)."""
    client = anthropic.Anthropic()

    system = SYSTEM_PROMPT + f"\n\nThe user's timezone is {user_timezone}."

    messages = list(history) + [{"role": "user", "content": user_message}]

    runner = client.beta.messages.tool_runner(
        model=MODEL,
        max_tokens=4096,
        system=system,
        tools=CLIENT_TOOLS + SERVER_TOOLS,
        messages=messages,
    )

    final_message = None
    for message in runner:
        final_message = message
        messages.append({"role": "assistant", "content": message.content})
        tool_response = runner.generate_tool_call_response()
        if tool_response is not None:
            messages.append(tool_response)

    assistant_text = ""
    if final_message is not None:
        for block in final_message.content:
            if block.type == "text":
                assistant_text += block.text

    # Return the updated history without the last assistant turn already appended,
    # so the caller stores exactly what was said.
    return assistant_text, messages


def load_history(user_id: str) -> list[dict[str, Any]]:
    from db import connect

    with connect() as conn:
        row = conn.execute(
            "SELECT messages_json FROM conversations WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    if row is None:
        return []
    return json.loads(row["messages_json"])


def save_history(user_id: str, messages: list[dict[str, Any]]) -> None:
    from db import connect, now

    # Serialize content blocks: SDK objects → dicts via model_dump when present.
    def serialize(msg: dict[str, Any]) -> dict[str, Any]:
        content = msg["content"]
        if isinstance(content, str):
            return {"role": msg["role"], "content": content}
        serialized_blocks = []
        for block in content:
            if hasattr(block, "model_dump"):
                serialized_blocks.append(block.model_dump())
            elif isinstance(block, dict):
                serialized_blocks.append(block)
            else:
                serialized_blocks.append({"type": "text", "text": str(block)})
        return {"role": msg["role"], "content": serialized_blocks}

    serialized = [serialize(m) for m in messages]
    payload = json.dumps(serialized)
    with connect() as conn:
        conn.execute(
            "INSERT INTO conversations (user_id, messages_json, updated_at) "
            "VALUES (?, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET messages_json = excluded.messages_json, "
            "updated_at = excluded.updated_at",
            (user_id, payload, now()),
        )


def reset_history(user_id: str) -> None:
    from db import connect

    with connect() as conn:
        conn.execute("DELETE FROM conversations WHERE user_id = ?", (user_id,))
