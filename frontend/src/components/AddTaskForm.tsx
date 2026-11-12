import { useState, type FormEvent } from "react";

export function AddTaskForm({ onAdd }: { onAdd: (title: string) => Promise<boolean> }) {
  const [title, setTitle] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const value = title.trim();
    if (!value || busy) return;
    setBusy(true);
    if (await onAdd(value)) setTitle("");
    setBusy(false);
  };

  return (
    <form className="add" onSubmit={submit}>
      <label className="sr-only" htmlFor="new-task">
        New task
      </label>
      <input
        id="new-task"
        value={title}
        maxLength={200}
        placeholder="What would you like to get done?"
        onChange={(e) => setTitle(e.target.value)}
      />
      <button type="submit" disabled={busy || !title.trim()}>
        + Add task
      </button>
    </form>
  );
}
