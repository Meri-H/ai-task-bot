import type { Task, TaskStatus } from "./types";

const TOKEN_KEY = "taskbot.token";

export class AuthError extends Error {}

export const tokenStore = {
  get: (): string => {
    try {
      return localStorage.getItem(TOKEN_KEY) ?? "";
    } catch {
      return "";
    }
  },
  set: (token: string): void => {
    try {
      localStorage.setItem(TOKEN_KEY, token);
    } catch {
      /* storage unavailable: token lives only for this page load */
    }
  },
  clear: (): void => {
    try {
      localStorage.removeItem(TOKEN_KEY);
    } catch {
      /* ignore */
    }
  },
};

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = tokenStore.get();
  const response = await fetch(`/api${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...init.headers,
    },
  });
  if (response.status === 401) throw new AuthError("Invalid or missing token");
  if (!response.ok) {
    const detail = await response.json().catch(() => null);
    throw new Error(typeof detail?.detail === "string" ? detail.detail : `Request failed (${response.status})`);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

const PAGE_SIZE = 500;

async function listAllTasks(): Promise<Task[]> {
  const tasks = new Map<number, Task>();
  let cursor: Task | undefined;
  while (true) {
    const params = new URLSearchParams({ limit: String(PAGE_SIZE) });
    if (cursor) {
      params.set("before_created_at", cursor.created_at);
      params.set("before_id", String(cursor.id));
    }
    const page = await request<{ items: Task[]; total: number }>(`/tasks?${params}`);
    for (const task of page.items) tasks.set(task.id, task);
    if (page.items.length < PAGE_SIZE) break;
    const last = page.items[page.items.length - 1];
    if (cursor?.id === last.id) throw new Error("Could not load the next page of tasks. Please refresh.");
    cursor = last;
  }
  return [...tasks.values()];
}

export const api = {
  deleteTask: (id: number) => request<void>(`/tasks/${id}`, { method: "DELETE" }),
  listTasks: listAllTasks,
  createTask: (title: string) => request<Task>("/tasks", { method: "POST", body: JSON.stringify({ title }) }),
  setStatus: (id: number, status: TaskStatus) =>
    request<Task>(`/tasks/${id}`, { method: "PATCH", body: JSON.stringify({ status }) }),
};

export function eventsUrl(): string {
  const scheme = window.location.protocol === "https:" ? "wss" : "ws";
  const token = tokenStore.get();
  const query = token ? `?token=${encodeURIComponent(token)}` : "";
  return `${scheme}://${window.location.host}/api/ws${query}`;
}
