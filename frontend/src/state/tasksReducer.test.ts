import { describe, expect, it } from "vitest";
import type { Task } from "../types";
import { initialState, tasksReducer } from "./tasksReducer";

const task = (id: number, overrides: Partial<Task> = {}): Task => ({
  id,
  title: `task ${id}`,
  description: null,
  status: "pending",
  source: "text",
  created_at: `2026-01-01T00:00:0${id}Z`,
  updated_at: `2026-01-01T00:00:0${id}Z`,
  completed_at: null,
  user: null,
  ...overrides,
});

describe("tasksReducer", () => {
  it("loads tasks newest first", () => {
    const state = tasksReducer(initialState, { type: "loaded", tasks: [task(1), task(3), task(2)] });
    expect(state.loaded).toBe(true);
    expect(state.tasks.map((t) => t.id)).toEqual([3, 2, 1]);
  });

  it("adds a task that arrives over the live connection", () => {
    const state = tasksReducer(
      tasksReducer(initialState, { type: "loaded", tasks: [task(1)] }),
      { type: "upsert", task: task(2, { source: "voice" }) },
    );
    expect(state.tasks.map((t) => t.id)).toEqual([2, 1]);
  });

  it("replaces an existing task with a newer version", () => {
    const loaded = tasksReducer(initialState, { type: "loaded", tasks: [task(1)] });
    const updated = task(1, { status: "completed", updated_at: "2026-01-01T00:10:00Z" });
    expect(tasksReducer(loaded, { type: "upsert", task: updated }).tasks[0].status).toBe("completed");
  });

  it("ignores a stale version so late responses cannot overwrite newer events", () => {
    const loaded = tasksReducer(initialState, {
      type: "loaded",
      tasks: [task(1, { status: "completed", updated_at: "2026-01-01T00:10:00Z" })],
    });
    const stale = task(1, { status: "pending", updated_at: "2026-01-01T00:05:00Z" });
    expect(tasksReducer(loaded, { type: "upsert", task: stale }).tasks[0].status).toBe("completed");
  });

  it("applies an optimistic status change and can roll it back with force", () => {
    const loaded = tasksReducer(initialState, { type: "loaded", tasks: [task(1)] });
    const optimistic = tasksReducer(loaded, { type: "optimisticStatus", id: 1, status: "in_progress" });
    expect(optimistic.tasks[0].status).toBe("in_progress");
    const rolledBack = tasksReducer(optimistic, { type: "force", task: task(1) });
    expect(rolledBack.tasks[0].status).toBe("pending");
  });
  it("removes a deleted task and ignores late responses or snapshots for it", () => {
    const loaded = tasksReducer(initialState, { type: "loaded", tasks: [task(1), task(2)] });
    const deleted = tasksReducer(loaded, { type: "deleted", id: 1 });
    expect(deleted.tasks.map((t) => t.id)).toEqual([2]);
    expect(tasksReducer(deleted, { type: "upsert", task: task(1) }).tasks).toEqual(deleted.tasks);
    expect(tasksReducer(deleted, { type: "force", task: task(1) }).tasks).toEqual(deleted.tasks);
    expect(tasksReducer(deleted, { type: "loaded", tasks: [task(1), task(2)] }).tasks).toEqual(deleted.tasks);
  });

  it("preserves live creations and newer updates received during a paginated load", () => {
    const started = tasksReducer(initialState, { type: "loadStarted" });
    const created = tasksReducer(started, { type: "upsert", task: task(3) });
    const updated = tasksReducer(created, { type: "upsert", task: task(1, {
      status: "completed", updated_at: "2026-01-01T01:00:00Z",
    }) });
    const loaded = tasksReducer(updated, { type: "loaded", tasks: [task(1), task(2)] });
    expect(loaded.tasks.map((t) => t.id)).toEqual([3, 2, 1]);
    expect(loaded.tasks.find((t) => t.id === 1)?.status).toBe("completed");
    expect(loaded.changesDuringLoad).toEqual([]);
  });

  it("keeps live data after a failed load and accepts a later successful refresh", () => {
    const started = tasksReducer(initialState, { type: "loadStarted" });
    const created = tasksReducer(started, { type: "upsert", task: task(1) });
    const failed = tasksReducer(created, { type: "loadFailed" });
    expect(failed.tasks).toEqual([task(1)]);
    expect(failed.loading).toBe(false);
    const retry = tasksReducer(failed, { type: "loadStarted" });
    expect(tasksReducer(retry, { type: "loaded", tasks: [task(1), task(2)] }).tasks).toHaveLength(2);
  });

});
