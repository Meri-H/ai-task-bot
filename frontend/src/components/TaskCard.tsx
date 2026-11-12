import { useState, type DragEvent } from "react";
import { STATUSES, type Task, type TaskStatus } from "../types";

interface Props {
  task: Task;
  fresh: boolean;
  onDelete: (id: number) => Promise<boolean>;
  onStatusChange: (id: number, status: TaskStatus) => void;
}

function SourceIcon({ source }: { source: Task["source"] }) {
  if (source === "voice") {
    return (
      <svg className="source source-voice" viewBox="0 0 20 20" role="img" aria-label="Created from a voice message">
        <title>Voice message</title>
        {[3, 6.5, 10, 13.5, 17].map((x, i) => (
          <rect key={x} x={x - 1} y={10 - [3, 6, 4, 7, 2][i]} width="2" height={[6, 12, 8, 14, 4][i]} rx="1" />
        ))}
      </svg>
    );
  }
  if (source === "text") {
    return (
      <svg className="source" viewBox="0 0 20 20" role="img" aria-label="Created from a Telegram message">
        <title>Telegram text message</title>
        <path d="M3 4h14v9H9l-4 3v-3H3z" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" />
      </svg>
    );
  }
  return (
    <svg className="source" viewBox="0 0 20 20" role="img" aria-label="Created on the dashboard">
      <title>Added on the dashboard</title>
      <path d="M10 4v12M4 10h12" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
    </svg>
  );
}

function formatTime(iso: string): string {
  return new Date(iso).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

export function TaskCard({ task, fresh, onStatusChange, onDelete }: Props) {
  const [confirming, setConfirming] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const remove = async () => {
    if (deleting) return;
    setDeleting(true);
    if (await onDelete(task.id)) setConfirming(false);
    setDeleting(false);
  };
  const onDragStart = (event: DragEvent<HTMLElement>) => {
    event.dataTransfer.setData("text/plain", String(task.id));
    event.dataTransfer.effectAllowed = "move";
  };

  return (
    <article
      className={`card${fresh ? " card-fresh" : ""}${task.source === "voice" ? " card-voice" : ""}`}
      draggable={!confirming && !deleting}
      aria-busy={deleting}
      onDragStart={onDragStart}
      aria-label={`Task ${task.id}: ${task.title}`}
    >
      <div className="card-head">
        <SourceIcon source={task.source} />
        <h3 className="card-title">{task.title}</h3>
        <button type="button" className="delete-icon" aria-label={`Delete task: ${task.title}`}
          title="Delete task" onClick={() => setConfirming(true)} disabled={deleting}>
          <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true">
            <path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13M10 10v7m4-7v7" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </button>
      </div>
      {task.description && <p className="card-description">{task.description}</p>}
      <p className="card-meta">
        #{task.id} · {task.user ? task.user.display_name : "Dashboard"} · {formatTime(task.created_at)}
      </p>
      {confirming && (
        <div className="delete-confirm" role="group" aria-label={`Confirm deletion of ${task.title}`}>
          <p>Delete this task? This cannot be undone.</p>
          <div><button type="button" autoFocus disabled={deleting} onClick={() => setConfirming(false)}>Cancel</button>
          <button type="button" className="danger-button" disabled={deleting} onClick={() => void remove()}>
            {deleting ? "Deleting…" : "Delete task"}
          </button></div>
        </div>
      )}
      <div className="toggle" role="group" aria-label={`Status of task ${task.id}`}>
        {STATUSES.map(({ id, label }) => (
          <button
            key={id}
            type="button"
            className={`toggle-btn toggle-${id}`}
            disabled={deleting || confirming}
            aria-pressed={task.status === id}
            onClick={() => onStatusChange(task.id, id)}
          >
            {label}
          </button>
        ))}
      </div>
    </article>
  );
}
