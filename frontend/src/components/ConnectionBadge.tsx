import type { ConnectionState } from "../types";

const LABELS: Record<ConnectionState, string> = {
  connecting: "Connecting…",
  live: "Live",
  reconnecting: "Reconnecting…",
};

export function ConnectionBadge({ state }: { state: ConnectionState }) {
  return (
    <span className={`badge badge-${state}`} role="status" aria-live="polite">
      <span className="badge-dot" aria-hidden="true" />
      {LABELS[state]}
    </span>
  );
}
