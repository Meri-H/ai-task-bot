import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { AuthError, api, eventsUrl } from "../api";
import { initialState, tasksReducer } from "../state/tasksReducer";
import type { ConnectionState, ServerEvent, TaskStatus } from "../types";

const HEARTBEAT_MS = 25_000;
const MAX_BACKOFF_MS = 15_000;
const HIGHLIGHT_MS = 4_000;

/** Loads tasks, keeps them live over a WebSocket, and exposes status changes. */
export function useTaskBoard() {
  const [state, dispatch] = useReducer(tasksReducer, initialState);
  const [connection, setConnection] = useState<ConnectionState>("connecting");
  const [needsAuth, setNeedsAuth] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [freshIds, setFreshIds] = useState<ReadonlySet<number>>(new Set());

  const refreshing = useRef<Promise<void> | null>(null);
  const refreshAgain = useRef(false);
  const refetch = useCallback(() => {
    if (refreshing.current) {
      // A newly opened/reconnected socket may have missed events before it connected.
      // Finish the current request, then take another snapshot with the socket listening.
      refreshAgain.current = true;
      return refreshing.current;
    }
    const fetchTasks = async () => {
      try {
        do {
          refreshAgain.current = false;
          dispatch({ type: "loadStarted" });
          try {
            dispatch({ type: "loaded", tasks: await api.listTasks() });
            setError(null);
            setNeedsAuth(false);
          } catch (e) {
            dispatch({ type: "loadFailed" });
            if (e instanceof AuthError) setNeedsAuth(true);
            else setError(e instanceof Error ? e.message : "Could not load tasks");
            refreshAgain.current = false;
          }
        } while (refreshAgain.current);
      } finally {
        refreshing.current = null;
      }
    };
    refreshing.current = fetchTasks();
    return refreshing.current;
  }, []);

  const highlight = useCallback((id: number) => {
    setFreshIds((prev) => new Set(prev).add(id));
    window.setTimeout(() => {
      setFreshIds((prev) => {
        const next = new Set(prev);
        next.delete(id);
        return next;
      });
    }, HIGHLIGHT_MS);
  }, []);

  useEffect(() => {
    void refetch();
  }, [refetch]);

  // Live connection with automatic reconnect.
  useEffect(() => {
    if (needsAuth) return;
    let closed = false;
    let socket: WebSocket | null = null;
    let retry = 0;
    let retryTimer: number | undefined;
    let heartbeat: number | undefined;

    const connect = () => {
      setConnection(retry === 0 ? "connecting" : "reconnecting");
      socket = new WebSocket(eventsUrl());

      socket.onopen = () => {
        retry = 0;
        setConnection("live");
        // Anything that happened while we were disconnected is picked up here.
        void refetch();
        heartbeat = window.setInterval(() => socket?.send("ping"), HEARTBEAT_MS);
      };

      socket.onmessage = (message) => {
        const event = JSON.parse(message.data) as ServerEvent;
        if (event.type === "task.created") {
          dispatch({ type: "upsert", task: event.task });
          highlight(event.task.id);
        } else if (event.type === "task.updated") {
          dispatch({ type: "upsert", task: event.task });
        } else if (event.type === "task.deleted") {
          dispatch({ type: "deleted", id: event.task_id });
        } else if (event.type === "resync") {
          void refetch();
        }
      };

      socket.onclose = (event) => {
        window.clearInterval(heartbeat);
        if (closed) return;
        if (event.code === 4401) {
          setNeedsAuth(true);
          return;
        }
        setConnection("reconnecting");
        retry += 1;
        retryTimer = window.setTimeout(connect, Math.min(1000 * 2 ** (retry - 1), MAX_BACKOFF_MS));
      };
    };

    connect();
    return () => {
      closed = true;
      window.clearTimeout(retryTimer);
      window.clearInterval(heartbeat);
      socket?.close();
    };
  }, [needsAuth, refetch, highlight]);

  const setStatus = useCallback(
    async (id: number, status: TaskStatus) => {
      const previous = state.tasks.find((t) => t.id === id);
      if (!previous || previous.status === status) return;
      dispatch({ type: "optimisticStatus", id, status });
      try {
        dispatch({ type: "upsert", task: await api.setStatus(id, status) });
      } catch (e) {
        dispatch({ type: "force", task: previous });
        setError(e instanceof Error ? e.message : "Could not update the task");
      }
    },
    [state.tasks],
  );

  const addTask = useCallback(async (title: string) => {
    try {
      dispatch({ type: "upsert", task: await api.createTask(title) });
      setError(null);
      return true;
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not add the task");
      return false;
    }
  }, []);

  const deleteTask = useCallback(async (id: number) => {
    try {
      await api.deleteTask(id);
      dispatch({ type: "deleted", id });
      setError(null);
      return true;
    } catch (e) {
      // Another dashboard may already have removed it.
      setError(e instanceof Error ? e.message : "Could not delete the task");
      return false;
    }
  }, []);

  const submitToken = useCallback(() => {
    void refetch(); // clears needsAuth on success, which also (re)starts the live connection
  }, [refetch]);

  return {
    tasks: state.tasks,
    loaded: state.loaded,
    connection,
    needsAuth,
    error,
    freshIds,
    setStatus,
    addTask,
    deleteTask,
    submitToken,
    dismissError: () => setError(null),
  };
}
