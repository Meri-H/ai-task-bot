import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { Task } from "../types";
import { Board } from "./Board";

const base: Task = {
  id: 1,
  title: "Call the dentist",
  description: null,
  status: "pending",
  source: "voice",
  created_at: "2026-01-01T10:00:00Z",
  updated_at: "2026-01-01T10:00:00Z",
  completed_at: null,
  user: { id: 1, username: "ann", first_name: "Ann", display_name: "Ann" },
};

describe("Board", () => {
  it("places each task in the column for its status", () => {
    const tasks = [
      base,
      { ...base, id: 2, title: "Ship it", status: "completed" as const },
      { ...base, id: 3, title: "Write docs", status: "in_progress" as const },
    ];
    render(<Board tasks={tasks} freshIds={new Set()} onDelete={async () => true} onStatusChange={() => {}} />);

    expect(within(screen.getByRole("region", { name: "Pending" })).getByText("Call the dentist")).toBeInTheDocument();
    expect(within(screen.getByRole("region", { name: "In progress" })).getByText("Write docs")).toBeInTheDocument();
    expect(within(screen.getByRole("region", { name: "Completed" })).getByText("Ship it")).toBeInTheDocument();
  });

  it("shows who created a task and how", () => {
    render(<Board tasks={[base]} freshIds={new Set()} onDelete={async () => true} onStatusChange={() => {}} />);
    expect(screen.getByText(/Ann/)).toBeInTheDocument();
    expect(screen.getByRole("img", { name: /voice message/i })).toBeInTheDocument();
  });

  it("changes status through the toggle", async () => {
    const onStatusChange = vi.fn();
    render(<Board tasks={[base]} freshIds={new Set()} onDelete={async () => true} onStatusChange={onStatusChange} />);

    const group = screen.getByRole("group", { name: "Status of task 1" });
    expect(within(group).getByRole("button", { name: "Pending" })).toHaveAttribute("aria-pressed", "true");

    await userEvent.click(within(group).getByRole("button", { name: "Completed" }));
    expect(onStatusChange).toHaveBeenCalledWith(1, "completed");
  });

  it("highlights tasks that just arrived", () => {
    render(<Board tasks={[base]} freshIds={new Set([1])} onDelete={async () => true} onStatusChange={() => {}} />);
    expect(screen.getByLabelText(/Task 1/)).toHaveClass("card-fresh");
  });

  it("invites the user to add something in an empty column", () => {
    render(<Board tasks={[]} freshIds={new Set()} onDelete={async () => true} onStatusChange={() => {}} />);
    expect(screen.getAllByText("Nothing here yet")).toHaveLength(3);
  });
  it("requires confirmation, supports cancel, and deletes the chosen task", async () => {
    const onDelete = vi.fn().mockResolvedValue(true);
    render(<Board tasks={[base]} freshIds={new Set()} onStatusChange={() => {}} onDelete={onDelete} />);
    await userEvent.click(screen.getByRole("button", { name: "Delete task: Call the dentist" }));
    expect(onDelete).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByText(/This cannot be undone/)).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Delete task: Call the dentist" }));
    await userEvent.click(screen.getByRole("button", { name: "Delete task" }));
    expect(onDelete).toHaveBeenCalledWith(1);
  });

  it("keeps the task and confirmation available if deletion fails", async () => {
    const onDelete = vi.fn().mockResolvedValue(false);
    render(<Board tasks={[base]} freshIds={new Set()} onStatusChange={() => {}} onDelete={onDelete} />);
    await userEvent.click(screen.getByRole("button", { name: "Delete task: Call the dentist" }));
    await userEvent.click(screen.getByRole("button", { name: "Delete task" }));
    expect(screen.getByText("Call the dentist")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Delete task" })).toBeEnabled();
  });

});
