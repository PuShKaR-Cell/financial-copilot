import * as SQLite from "expo-sqlite";

let dbPromise: Promise<SQLite.SQLiteDatabase> | null = null;

async function getDb(): Promise<SQLite.SQLiteDatabase> {
  if (!dbPromise) {
    dbPromise = (async () => {
      const db = await SQLite.openDatabaseAsync("assistant.db");
      await db.execAsync(`
        PRAGMA journal_mode = WAL;
        CREATE TABLE IF NOT EXISTS memories (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          content TEXT NOT NULL,
          tags TEXT NOT NULL DEFAULT '',
          created_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS reminders (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          text TEXT NOT NULL,
          remind_at INTEGER NOT NULL,
          created_at INTEGER NOT NULL,
          done INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS chat_messages (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          role TEXT NOT NULL,
          content TEXT NOT NULL,
          created_at INTEGER NOT NULL
        );
      `);
      return db;
    })();
  }
  return dbPromise;
}

export type Memory = {
  id: number;
  content: string;
  tags: string;
  created_at: number;
};

export type Reminder = {
  id: number;
  text: string;
  remind_at: number;
  created_at: number;
  done: boolean;
};

export type ChatMessage = {
  id: number;
  role: "user" | "assistant" | "system" | "tool";
  content: string;
  created_at: number;
};

// ---- Memories ----

export async function saveMemory(content: string, tags = ""): Promise<Memory> {
  const db = await getDb();
  const created_at = Date.now();
  const result = await db.runAsync(
    "INSERT INTO memories (content, tags, created_at) VALUES (?, ?, ?)",
    content.trim(),
    tags.trim(),
    created_at,
  );
  return {
    id: result.lastInsertRowId,
    content: content.trim(),
    tags: tags.trim(),
    created_at,
  };
}

export async function listMemories(query = "", limit = 50): Promise<Memory[]> {
  const db = await getDb();
  if (query) {
    const like = `%${query}%`;
    return await db.getAllAsync<Memory>(
      "SELECT id, content, tags, created_at FROM memories WHERE content LIKE ? OR tags LIKE ? ORDER BY created_at DESC LIMIT ?",
      like,
      like,
      limit,
    );
  }
  return await db.getAllAsync<Memory>(
    "SELECT id, content, tags, created_at FROM memories ORDER BY created_at DESC LIMIT ?",
    limit,
  );
}

export async function deleteMemory(id: number): Promise<boolean> {
  const db = await getDb();
  const result = await db.runAsync("DELETE FROM memories WHERE id = ?", id);
  return result.changes > 0;
}

// ---- Reminders ----

export async function createReminder(text: string, remindAtMs: number): Promise<Reminder> {
  const db = await getDb();
  const created_at = Date.now();
  const result = await db.runAsync(
    "INSERT INTO reminders (text, remind_at, created_at) VALUES (?, ?, ?)",
    text.trim(),
    remindAtMs,
    created_at,
  );
  return {
    id: result.lastInsertRowId,
    text: text.trim(),
    remind_at: remindAtMs,
    created_at,
    done: false,
  };
}

export async function listReminders(includeDone = false): Promise<Reminder[]> {
  const db = await getDb();
  const rows = includeDone
    ? await db.getAllAsync<{ id: number; text: string; remind_at: number; created_at: number; done: number }>(
        "SELECT id, text, remind_at, created_at, done FROM reminders ORDER BY remind_at ASC",
      )
    : await db.getAllAsync<{ id: number; text: string; remind_at: number; created_at: number; done: number }>(
        "SELECT id, text, remind_at, created_at, done FROM reminders WHERE done = 0 ORDER BY remind_at ASC",
      );
  return rows.map((r) => ({ ...r, done: !!r.done }));
}

export async function completeReminder(id: number): Promise<boolean> {
  const db = await getDb();
  const result = await db.runAsync("UPDATE reminders SET done = 1 WHERE id = ?", id);
  return result.changes > 0;
}

// ---- Chat history ----

export async function appendChat(role: ChatMessage["role"], content: string): Promise<void> {
  const db = await getDb();
  await db.runAsync(
    "INSERT INTO chat_messages (role, content, created_at) VALUES (?, ?, ?)",
    role,
    content,
    Date.now(),
  );
}

export async function loadChat(limit = 200): Promise<ChatMessage[]> {
  const db = await getDb();
  return await db.getAllAsync<ChatMessage>(
    "SELECT id, role, content, created_at FROM chat_messages ORDER BY id ASC LIMIT ?",
    limit,
  );
}

export async function resetChat(): Promise<void> {
  const db = await getDb();
  await db.runAsync("DELETE FROM chat_messages");
}
