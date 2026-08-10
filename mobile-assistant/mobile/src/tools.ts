import * as db from "./db";
import { getSearxngUrl } from "./config";

export type ToolDef = {
  name: string;
  description: string;
  parameters: Record<string, { type: string; description: string; enum?: string[]; default?: unknown }>;
  required: string[];
  run(args: Record<string, unknown>): Promise<string>;
};

function iso(ms: number): string {
  return new Date(ms).toISOString();
}

export const TOOLS: ToolDef[] = [
  {
    name: "save_memory",
    description:
      "Persist a fact the user wants remembered (preferences, names, context). Call this when the user says 'remember X' or when a durable detail comes up.",
    parameters: {
      content: { type: "string", description: "What to remember, in the user's own words when possible." },
      tags: { type: "string", description: "Comma-separated tags for later retrieval." },
    },
    required: ["content"],
    async run(args) {
      const m = await db.saveMemory(
        String(args.content ?? ""),
        String(args.tags ?? ""),
      );
      return `Saved memory #${m.id}.`;
    },
  },

  {
    name: "list_memories",
    description:
      "List saved memories, optionally filtered by a substring. Call this before answering questions that depend on prior context.",
    parameters: {
      query: { type: "string", description: "Substring to filter by (empty = most recent)." },
      limit: { type: "integer", description: "Max results (default 20)." },
    },
    required: [],
    async run(args) {
      const rows = await db.listMemories(
        String(args.query ?? ""),
        Number(args.limit ?? 20),
      );
      if (rows.length === 0) return "No memories.";
      return JSON.stringify(
        rows.map((r) => ({
          id: r.id,
          content: r.content,
          tags: r.tags,
          created_at: iso(r.created_at),
        })),
      );
    },
  },

  {
    name: "delete_memory",
    description: "Delete a memory by id. Confirm with the user first.",
    parameters: {
      memory_id: { type: "integer", description: "The memory's id from list_memories." },
    },
    required: ["memory_id"],
    async run(args) {
      const id = Number(args.memory_id);
      const ok = await db.deleteMemory(id);
      return ok ? `Deleted memory #${id}.` : `No memory with id ${id}.`;
    },
  },

  {
    name: "current_time",
    description:
      "Return the current time in the user's local timezone. Call this before creating a reminder so relative times like 'tomorrow at 9am' resolve to a concrete timestamp.",
    parameters: {},
    required: [],
    async run() {
      return new Date().toISOString();
    },
  },

  {
    name: "create_reminder",
    description:
      "Schedule a reminder. Always call current_time first, then pass an ISO-8601 timestamp for remind_at_iso.",
    parameters: {
      text: { type: "string", description: "What to remind the user about." },
      remind_at_iso: {
        type: "string",
        description: "ISO-8601 timestamp with timezone, e.g. 2026-08-11T14:30:00-04:00.",
      },
    },
    required: ["text", "remind_at_iso"],
    async run(args) {
      const text = String(args.text ?? "");
      const iso = String(args.remind_at_iso ?? "");
      const ms = Date.parse(iso);
      if (Number.isNaN(ms)) return `Invalid timestamp: ${iso}. Use ISO-8601.`;
      if (ms <= Date.now()) return `Time ${iso} is in the past.`;
      const r = await db.createReminder(text, ms);
      return `Reminder #${r.id} set for ${new Date(ms).toISOString()}.`;
    },
  },

  {
    name: "list_reminders",
    description: "List reminders (active by default).",
    parameters: {
      include_done: { type: "boolean", description: "Include completed reminders." },
    },
    required: [],
    async run(args) {
      const rows = await db.listReminders(!!args.include_done);
      if (rows.length === 0) return "No reminders.";
      return JSON.stringify(
        rows.map((r) => ({
          id: r.id,
          text: r.text,
          remind_at: iso(r.remind_at),
          done: r.done,
        })),
      );
    },
  },

  {
    name: "complete_reminder",
    description: "Mark a reminder as done.",
    parameters: {
      reminder_id: { type: "integer", description: "The reminder id." },
    },
    required: ["reminder_id"],
    async run(args) {
      const id = Number(args.reminder_id);
      const ok = await db.completeReminder(id);
      return ok ? `Marked reminder #${id} done.` : `No reminder with id ${id}.`;
    },
  },

  {
    name: "web_search",
    description:
      "Search the web via the user's self-hosted SearXNG instance. Returns the top results as JSON.",
    parameters: {
      query: { type: "string", description: "Search query." },
      num_results: { type: "integer", description: "Max results (default 5)." },
    },
    required: ["query"],
    async run(args) {
      const query = String(args.query ?? "").trim();
      if (!query) return "Empty query.";
      const n = Math.max(1, Math.min(10, Number(args.num_results ?? 5)));
      const base = await getSearxngUrl();
      const url = `${base.replace(/\/$/, "")}/search?q=${encodeURIComponent(query)}&format=json&safesearch=0`;
      try {
        const res = await fetch(url, {
          headers: { Accept: "application/json" },
        });
        if (!res.ok) return `Search failed: ${res.status} ${res.statusText}`;
        const data = (await res.json()) as {
          results?: Array<{ title?: string; url?: string; content?: string }>;
        };
        const results = (data.results ?? []).slice(0, n).map((r) => ({
          title: r.title ?? "",
          url: r.url ?? "",
          snippet: r.content ?? "",
        }));
        if (results.length === 0) return "No results.";
        return JSON.stringify(results);
      } catch (err) {
        return `Search error: ${(err as Error).message}. Is SearXNG reachable?`;
      }
    },
  },
];

export const TOOLS_BY_NAME: Record<string, ToolDef> = Object.fromEntries(
  TOOLS.map((t) => [t.name, t]),
);
