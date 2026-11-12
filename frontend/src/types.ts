export type TaskStatus = "pending" | "in_progress" | "completed";
export type TaskSource = "text" | "voice" | "web";

export interface TaskAuthor {
  id: number;
  username: string | null;
  first_name: string | null;
  display_name: string;
}

export interface Task {
  id: number;
  title: string;
  description: string | null;
  status: TaskStatus;
  source: TaskSource;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
  user: TaskAuthor | null;
}

export type ServerEvent =
  | { type: "task.created"; task: Task }
  | { type: "task.updated"; task: Task }
  | { type: "task.deleted"; task_id: number }
  | { type: "resync" }
  | { type: "pong" };

export const STATUSES: { id: TaskStatus; label: string }[] = [
  { id: "pending", label: "Pending" },
  { id: "in_progress", label: "In progress" },
  { id: "completed", label: "Completed" },
];

export type ConnectionState = "connecting" | "live" | "reconnecting";
