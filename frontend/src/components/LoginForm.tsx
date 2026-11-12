import { useState, type FormEvent } from "react";
import { tokenStore } from "../api";

export function LoginForm({ onSubmit }: { onSubmit: () => void }) {
  const [token, setToken] = useState("");
  const submit = (event: FormEvent) => {
    event.preventDefault();
    tokenStore.set(token.trim());
    onSubmit();
  };
  return (
    <form className="login" onSubmit={submit}>
      <h2>Enter the access token</h2>
      <p>This dashboard is protected. Use the DASHBOARD_TOKEN from your .env file.</p>
      <label className="sr-only" htmlFor="token">
        Access token
      </label>
      <input id="token" type="password" autoFocus value={token} onChange={(e) => setToken(e.target.value)} />
      <button type="submit" disabled={!token.trim()}>
        Open dashboard
      </button>
    </form>
  );
}
