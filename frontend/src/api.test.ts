import { afterEach, describe, expect, it, vi } from "vitest";
import { api, AuthError } from "./api";
import type { Task } from "./types";

const task = (id: number): Task => ({
  id, title: `Task ${id}`, description: null, source: "web", status: "pending", user: null,
  created_at: "2026-10-24T10:00:00.123456Z", updated_at: "2026-10-24T10:00:00.123456Z", completed_at: null,
});
const page = (items: Task[], total: number) => ({ ok: true, status: 200, json: async () => ({ items, total }) });

afterEach(() => vi.unstubAllGlobals());

describe("task API", () => {
  it("loads over 1,000 tasks and sends a stable cursor with full timestamp precision", async () => {
    const tasks = Array.from({ length: 1001 }, (_, i) => task(1001 - i));
    const fetch = vi.fn()
      .mockResolvedValueOnce(page(tasks.slice(0, 500), 1001))
      .mockResolvedValueOnce(page(tasks.slice(500, 1000), 1001))
      .mockResolvedValueOnce(page(tasks.slice(1000), 1001));
    vi.stubGlobal("fetch", fetch);
    expect((await api.listTasks()).map((t) => t.id)).toEqual(tasks.map((t) => t.id));
    const second = new URL(fetch.mock.calls[1][0], "http://localhost");
    expect(second.searchParams.get("before_id")).toBe("502");
    expect(second.searchParams.get("before_created_at")).toBe(tasks[499].created_at);
    expect(fetch).toHaveBeenCalledTimes(3);
  });

  it("finishes an exact-size page with an empty follow-up page", async () => {
    const fetch = vi.fn()
      .mockResolvedValueOnce(page(Array.from({ length: 500 }, (_, i) => task(500 - i)), 500))
      .mockResolvedValueOnce(page([], 500));
    vi.stubGlobal("fetch", fetch);
    expect(await api.listTasks()).toHaveLength(500);
    expect(fetch).toHaveBeenCalledTimes(2);
  });

  it("reports a failed later page instead of treating a partial board as complete", async () => {
    vi.stubGlobal("fetch", vi.fn()
      .mockResolvedValueOnce(page(Array.from({ length: 500 }, (_, i) => task(501 - i)), 501))
      .mockResolvedValueOnce({ ok: false, status: 503, json: async () => ({ detail: "Please retry" }) }));
    await expect(api.listTasks()).rejects.toThrow("Please retry");
  });

  it("does not loop forever if a server ignores the cursor", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
      page(Array.from({ length: 500 }, (_, i) => task(500 - i)), 501),
    ));
    await expect(api.listTasks()).rejects.toThrow("Could not load the next page");
  });

  it("handles a bodyless deletion response", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, status: 204 }));
    await expect(api.deleteTask(4)).resolves.toBeUndefined();
  });

  it("requires authentication when the API rejects the token", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 401 }));
    await expect(api.listTasks()).rejects.toBeInstanceOf(AuthError);
  });
});
