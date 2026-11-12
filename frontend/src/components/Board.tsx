import { useState, type DragEvent } from "react";
import { STATUSES, type Task, type TaskStatus } from "../types";
import { TaskCard } from "./TaskCard";

interface Props {
  tasks: Task[];
  freshIds: ReadonlySet<number>;
  onDelete: (id: number) => Promise<boolean>;
  onStatusChange: (id: number, status: TaskStatus) => void;
}

export function Board({ tasks, freshIds, onStatusChange, onDelete }: Props) {
  const [dropTarget, setDropTarget] = useState<TaskStatus | null>(null);

  const onDrop = (event: DragEvent<HTMLElement>, status: TaskStatus) => {
    event.preventDefault();
    setDropTarget(null);
    const id = Number(event.dataTransfer.getData("text/plain"));
    if (id) onStatusChange(id, status);
  };

  return (
    <div className="board">
      {STATUSES.map(({ id: status, label }) => {
        const column = tasks.filter((t) => t.status === status);
        return (
          <section
            key={status}
            className={`column column-${status}${dropTarget === status ? " column-drop" : ""}`}
            aria-labelledby={`col-${status}`}
            onDragOver={(e) => {
              e.preventDefault();
              setDropTarget(status);
            }}
            onDragLeave={() => setDropTarget((current) => (current === status ? null : current))}
            onDrop={(e) => onDrop(e, status)}
          >
            <header className="column-head">
              <h2 id={`col-${status}`}>{label}</h2>
              <span className="count" aria-label={`${column.length} tasks`}>
                {column.length}
              </span>
            </header>
            <div className="column-body">
              {column.map((task) => (
                <TaskCard key={task.id} task={task} fresh={freshIds.has(task.id)} onStatusChange={onStatusChange} onDelete={onDelete} />
              ))}
              {column.length === 0 && <p className="column-empty">Nothing here yet</p>}
            </div>
          </section>
        );
      })}
    </div>
  );
}
