"""Tool functions the assistant can call. Each returns a plain string for Claude."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from db import connect, now


def _user_id() -> str:
    # Single-user for now; extend to per-device later.
    return "default"


def save_memory(content: str, tags: str = "") -> str:
    """Persist a memory the user wants remembered."""
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO memories (user_id, content, tags, created_at) VALUES (?, ?, ?, ?)",
            (_user_id(), content.strip(), tags.strip(), now()),
        )
        return f"Saved memory #{cur.lastrowid}: {content.strip()}"


def list_memories(query: str = "", limit: int = 20) -> str:
    """Return recent memories, optionally filtered by a substring match."""
    with connect() as conn:
        if query:
            rows = conn.execute(
                "SELECT id, content, tags, created_at FROM memories "
                "WHERE user_id = ? AND (content LIKE ? OR tags LIKE ?) "
                "ORDER BY created_at DESC LIMIT ?",
                (_user_id(), f"%{query}%", f"%{query}%", limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, content, tags, created_at FROM memories "
                "WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
                (_user_id(), limit),
            ).fetchall()
    if not rows:
        return "No memories found."
    return json.dumps(
        [
            {
                "id": r["id"],
                "content": r["content"],
                "tags": r["tags"],
                "created_at": datetime.fromtimestamp(r["created_at"], tz=timezone.utc).isoformat(),
            }
            for r in rows
        ],
        indent=2,
    )


def delete_memory(memory_id: int) -> str:
    with connect() as conn:
        cur = conn.execute(
            "DELETE FROM memories WHERE user_id = ? AND id = ?",
            (_user_id(), memory_id),
        )
    if cur.rowcount == 0:
        return f"No memory with id {memory_id}."
    return f"Deleted memory #{memory_id}."


def create_reminder(text: str, remind_at_iso: str) -> str:
    """Create a reminder. remind_at_iso is ISO-8601 (e.g. 2026-08-11T14:30:00-04:00).

    The mobile app schedules the local notification once it fetches the reminder;
    the backend just persists.
    """
    try:
        remind_at = datetime.fromisoformat(remind_at_iso)
    except ValueError:
        return f"Invalid ISO timestamp: {remind_at_iso!r}. Use e.g. 2026-08-11T14:30:00-04:00."
    if remind_at.tzinfo is None:
        remind_at = remind_at.replace(tzinfo=timezone.utc)
    remind_ts = remind_at.timestamp()
    if remind_ts <= now():
        return f"Reminder time {remind_at.isoformat()} is in the past."
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO reminders (user_id, text, remind_at, created_at) VALUES (?, ?, ?, ?)",
            (_user_id(), text.strip(), remind_ts, now()),
        )
        return f"Reminder #{cur.lastrowid} set for {remind_at.isoformat()}: {text.strip()}"


def list_reminders(include_done: bool = False) -> str:
    with connect() as conn:
        if include_done:
            rows = conn.execute(
                "SELECT id, text, remind_at, done FROM reminders "
                "WHERE user_id = ? ORDER BY remind_at ASC",
                (_user_id(),),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, text, remind_at, done FROM reminders "
                "WHERE user_id = ? AND done = 0 ORDER BY remind_at ASC",
                (_user_id(),),
            ).fetchall()
    if not rows:
        return "No reminders."
    return json.dumps(
        [
            {
                "id": r["id"],
                "text": r["text"],
                "remind_at": datetime.fromtimestamp(r["remind_at"], tz=timezone.utc).isoformat(),
                "done": bool(r["done"]),
            }
            for r in rows
        ],
        indent=2,
    )


def complete_reminder(reminder_id: int) -> str:
    with connect() as conn:
        cur = conn.execute(
            "UPDATE reminders SET done = 1 WHERE user_id = ? AND id = ?",
            (_user_id(), reminder_id),
        )
    if cur.rowcount == 0:
        return f"No reminder with id {reminder_id}."
    return f"Marked reminder #{reminder_id} done."


def current_time(timezone_name: str = "UTC") -> str:
    """Return the current time in the given IANA timezone. Use before creating reminders."""
    try:
        tz = ZoneInfo(timezone_name)
    except Exception:
        return f"Unknown timezone {timezone_name!r}. Use IANA names like America/New_York."
    now_dt = datetime.now(tz)
    return now_dt.isoformat()
