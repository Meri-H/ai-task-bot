import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { api } from "../api";
import type { Task } from "../types";
import { useTaskBoard } from "./useTaskBoard";

const task: Task = {
  id: 1, title: "Arrived before the socket connected", description: null,
  status: "pending", source: "text", user: null,
  created_at: "2026-10-24T10:00:00Z", updated_at: "2026-10-24T10:00:00Z", completed_at: null,
};

class FakeSocket {
  static latest: FakeSocket;
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onclose: ((event: { code: number }) => void) | null = null;
  send = vi.fn();
  close = vi.fn();
  constructor() { FakeSocket.latest = this; }
}

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it("takes another snapshot if the socket opens during the initial paginated load", async () => {
  let finishFirst!: (tasks: Task[]) => void;
  const first = new Promise<Task[]>((resolve) => { finishFirst = resolve; });
  const list = vi.spyOn(api, "listTasks").mockReturnValueOnce(first).mockResolvedValueOnce([task]);
  vi.stubGlobal("WebSocket", FakeSocket);
  const { result, unmount } = renderHook(() => useTaskBoard());
  act(() => FakeSocket.latest.onopen?.());
  // The task was created after the first snapshot but before the socket was listening.
  // No WebSocket event is delivered for it, so a second REST request must recover it.
  await act(async () => { finishFirst([]); });
  await waitFor(() => expect(result.current.tasks).toEqual([task]));
  expect(list).toHaveBeenCalledTimes(2);
  unmount();
});

it("keeps a live task if an in-flight snapshot does not contain it yet", async () => {
  let finish!: (tasks: Task[]) => void;
  vi.spyOn(api, "listTasks").mockReturnValueOnce(new Promise((resolve) => { finish = resolve; }));
  vi.stubGlobal("WebSocket", FakeSocket);
  const { result, unmount } = renderHook(() => useTaskBoard());
  act(() => FakeSocket.latest.onmessage?.({ data: JSON.stringify({ type: "task.created", task }) }));
  await act(async () => { finish([]); });
  await waitFor(() => expect(result.current.loaded).toBe(true));
  expect(result.current.tasks).toEqual([task]);
  unmount();
});
