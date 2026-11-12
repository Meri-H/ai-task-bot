import type { Task, TaskStatus } from "../types";

export interface TasksState {
  tasks: Task[];
  loaded: boolean;
  loading: boolean;
  changesDuringLoad: Task[];
  deletedIds: ReadonlySet<number>;
}

export type TasksAction =
  | { type: "loadStarted" }
  | { type: "loadFailed" }
  | { type: "deleted"; id: number }
  | { type: "loaded"; tasks: Task[] }
  | { type: "upsert"; task: Task }
  | { type: "force"; task: Task }
  | { type: "optimisticStatus"; id: number; status: TaskStatus };

export const initialState: TasksState = {
  tasks: [], loaded: false, loading: false, changesDuringLoad: [], deletedIds: new Set(),
};

const byNewest = (a: Task, b: Task) => b.created_at.localeCompare(a.created_at) || b.id - a.id;

/**
 * Pure state transitions for the board.
 *
 * `upsert` ignores a task version older than the one we hold, so a slow REST response can never
 * overwrite a newer WebSocket event (events and responses can arrive in either order).
 * `force` bypasses that check; it is used to roll back a failed optimistic update.
 */
export function tasksReducer(state: TasksState, action: TasksAction): TasksState {
  switch (action.type) {
    case "deleted":
      return { ...state, tasks: state.tasks.filter((t) => t.id !== action.id),
        deletedIds: new Set(state.deletedIds).add(action.id) };

    case "loadStarted":
      return { ...state, loading: true, changesDuringLoad: [] };

    case "loadFailed":
      return { ...state, loading: false, changesDuringLoad: [] };

    case "loaded": {
      const tasks = new Map(action.tasks.map((t) => [t.id, t]));
      for (const changed of state.changesDuringLoad) {
        const fetched = tasks.get(changed.id);
        if (!fetched || changed.updated_at >= fetched.updated_at) tasks.set(changed.id, changed);
      }
      return {
        ...state, tasks: [...tasks.values()].filter((t) => !state.deletedIds.has(t.id)).sort(byNewest),
        loaded: true, loading: false, changesDuringLoad: [],
      };
    }

    case "upsert":
    case "force": {
      if (state.deletedIds.has(action.task.id)) return state;
      const existing = state.tasks.find((t) => t.id === action.task.id);
      if (existing && action.type === "upsert" && action.task.updated_at < existing.updated_at) return state;
      const changesDuringLoad = state.loading
        ? [...state.changesDuringLoad.filter((t) => t.id !== action.task.id), action.task]
        : state.changesDuringLoad;
      const tasks = existing
        ? state.tasks.map((t) => (t.id === action.task.id ? action.task : t))
        : [action.task, ...state.tasks].sort(byNewest);
      return { ...state, tasks, changesDuringLoad };
    }

    case "optimisticStatus":
      return {
        ...state,
        tasks: state.tasks.map((t) => (t.id === action.id ? { ...t, status: action.status } : t)),
      };
  }
}
