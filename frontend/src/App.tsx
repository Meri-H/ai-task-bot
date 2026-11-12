import { useState } from "react";
import { AddTaskForm } from "./components/AddTaskForm";
import { Board } from "./components/Board";
import { ConnectionBadge } from "./components/ConnectionBadge";
import { LoginForm } from "./components/LoginForm";
import { useTaskBoard } from "./hooks/useTaskBoard";

export default function App() {
  const board = useTaskBoard();
  const [query, setQuery] = useState("");
  const completed = board.tasks.filter((t) => t.status === "completed").length;
  const tasks = board.tasks.filter((t) =>
    `${t.title} ${t.description ?? ""} ${t.user?.display_name ?? "Dashboard"}`.toLowerCase().includes(query.trim().toLowerCase()),
  );

  return (
    <div className="shell">
      <header className="topbar">
        <a className="brand" href="/" aria-label="Taskbot home">
          <span className="brand-mark" aria-hidden="true">✓</span> Taskbot<span className="brand-divider">/</span><span className="workspace-label">Workspace</span>
        </a>
        <ConnectionBadge state={board.connection} />
      </header>
      {board.needsAuth ? <LoginForm onSubmit={board.submitToken} /> : (
        <main>
          <section className="overview" aria-label="Board overview">
            <div><p className="eyebrow">YOUR DAILY SPACE</p><h1>A little more focused.</h1>
              <p className="subtitle">Capture a thought. Make it happen.</p></div>
            <div className="summary" aria-label="Task summary">
              <span><strong>{board.tasks.length}</strong> total tasks</span>
              <span className="summary-divider" />
              <span><strong>{completed}</strong> completed</span>
            </div>
          </section>
          <div className="composer"><AddTaskForm onAdd={board.addTask} />
            <p className="composer-hint">Have your hands full? Send a text or voice note to your Telegram bot.</p>
          </div>
          {board.error && <div className="error" role="alert"><span>{board.error}</span>
            <button type="button" onClick={board.dismissError}>Dismiss</button></div>}
          <div className="board-toolbar">
            <div className="board-heading"><h2>Your board</h2><span className="toolbar-note">Drag tasks to move them</span></div>
            <label className="search"><svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden="true"><circle cx="10.5" cy="10.5" r="6.5" /><path d="m16 16 4 4" /></svg>
              <span className="sr-only">Search tasks</span><input type="search" placeholder="Search tasks…" value={query} onChange={(e) => setQuery(e.target.value)} />
            </label>
          </div>
          {!board.loaded && !board.error ? <p className="loading" role="status">Loading your tasks…</p> : (
            <Board tasks={tasks} freshIds={board.freshIds} onStatusChange={board.setStatus} onDelete={board.deleteTask} />
          )}
          {query && <p className="search-result" role="status">{tasks.length} matching {tasks.length === 1 ? "task" : "tasks"}
            <button type="button" onClick={() => setQuery("")}>Clear search</button></p>}
        </main>
      )}
    </div>
  );
}
