import Constants from "expo-constants";

const API_URL: string =
  (Constants.expoConfig?.extra?.apiUrl as string | undefined) ??
  "http://localhost:8000";

export type Memory = {
  id: number;
  content: string;
  tags: string;
  created_at: string;
};

export type Reminder = {
  id: number;
  text: string;
  remind_at: string;
  done: boolean;
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
  });
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new Error(`${res.status} ${res.statusText}: ${body}`);
  }
  if (res.status === 204) return undefined as unknown as T;
  return (await res.json()) as T;
}

export function chat(message: string, timezone: string): Promise<{ reply: string }> {
  return request("/chat", {
    method: "POST",
    body: JSON.stringify({ message, timezone }),
  });
}

export function resetChat(): Promise<{ status: string }> {
  return request("/chat/reset", { method: "POST" });
}

export function listMemories(query = ""): Promise<Memory[]> {
  const qs = query ? `?query=${encodeURIComponent(query)}` : "";
  return request(`/memories${qs}`);
}

export function deleteMemory(id: number): Promise<{ status: string }> {
  return request(`/memories/${id}`, { method: "DELETE" });
}

export function listReminders(includeDone = false): Promise<Reminder[]> {
  return request(`/reminders?include_done=${includeDone}`);
}

export function completeReminder(id: number): Promise<{ status: string }> {
  return request(`/reminders/${id}/complete`, { method: "POST" });
}

export { API_URL };
