"use client";

import { useAuth } from "@clerk/nextjs";
import { useState } from "react";

import { ApiClientError } from "@/lib/api/client";
import { saveGithubToken } from "@/lib/api/settings";

export function SettingsPanel() {
  const { getToken } = useAuth();
  const [githubToken, setGithubToken] = useState("");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<{ code: string; message: string } | null>(null);

  async function submit(event: React.FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    const value = githubToken.trim();
    if (!value) {
      setError({ code: "VALIDATION_ERROR", message: "Enter a GitHub personal access token." });
      return;
    }
    setSaving(true);
    setSaved(false);
    setError(null);
    try {
      const token = await getToken();
      if (!token) {
        throw new Error("Your session token is unavailable. Sign in again.");
      }
      await saveGithubToken(token, value);
      setGithubToken("");
      setSaved(true);
    } catch (caught: unknown) {
      setError({
        code: caught instanceof ApiClientError ? caught.code : "SETTINGS_SAVE_FAILED",
        message: caught instanceof Error ? caught.message : "The GitHub token could not be saved.",
      });
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="mx-auto max-w-4xl space-y-8">
      <header className="border-b border-hairline pb-6">
        <p className="font-mono text-xs uppercase tracking-[0.2em] text-indigo-300">Workspace / config</p>
        <h1 className="mt-3 text-2xl font-semibold">Settings</h1>
        <p className="mt-2 text-sm leading-6 text-secondaryText">Manage integrations used by repository ingestion and pull-request automation.</p>
      </header>

      <section className="rounded-lg border border-hairline bg-panel p-6">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <p className="font-mono text-[10px] uppercase tracking-[0.16em] text-indigo-300">GitHub integration</p>
            <h2 className="mt-2 text-lg font-semibold">Personal access token</h2>
            <p className="mt-2 max-w-2xl text-sm leading-6 text-secondaryText">The token is encrypted server-side and is never returned to the browser after it is saved.</p>
          </div>
          <span className="rounded-full border border-amber-500/30 bg-amber-500/10 px-2 py-1 font-mono text-[10px] text-amber-300">WRITE ONLY</span>
        </div>
        <form className="mt-6 max-w-xl" onSubmit={submit}>
          <label htmlFor="github-token" className="mb-2 block font-mono text-xs text-secondaryText">GitHub token</label>
          <input
            id="github-token"
            type="password"
            autoComplete="new-password"
            value={githubToken}
            onChange={(event) => { setGithubToken(event.target.value); setSaved(false); setError(null); }}
            placeholder="ghp_… or github_pat_…"
            aria-describedby="github-token-help"
            className="w-full rounded-md border border-hairline bg-surface px-3 py-2.5 font-mono text-sm text-primaryText placeholder:text-mutedText"
          />
          <p id="github-token-help" className="mt-2 text-xs leading-5 text-mutedText">Use a token with the minimum repository permissions required for cloning and pull-request creation.</p>
          {error ? <p className="mt-3 text-xs text-red-400" role="alert">{error.code}: {error.message}</p> : null}
          {saved ? <p className="mt-3 text-xs text-emerald-300" role="status">GitHub token saved securely.</p> : null}
          <button type="submit" disabled={saving} className="mt-5 rounded-md bg-indigo-600 px-4 py-2.5 text-sm font-semibold text-white hover:bg-indigo-500 disabled:cursor-wait disabled:opacity-60">
            {saving ? "Saving securely…" : "Save GitHub token"}
          </button>
        </form>
      </section>

      <section className="rounded-lg border border-hairline bg-panel p-6">
        <p className="font-mono text-[10px] uppercase tracking-[0.16em] text-secondaryText">LLM configuration</p>
        <h2 className="mt-2 text-lg font-semibold">Deployment-managed providers</h2>
        <p className="mt-2 max-w-2xl text-sm leading-6 text-secondaryText">Provider credentials and model choices are managed by the deployment environment. Secret values are intentionally not exposed through this browser settings surface.</p>
        <div className="mt-5 grid gap-3 sm:grid-cols-2">
          <ConfigRow label="Embedding model" value="Deployment configured" />
          <ConfigRow label="Refactor model" value="Deployment configured" />
          <ConfigRow label="CRAG provider" value="Deployment configured" />
          <ConfigRow label="Sandbox policy" value="Docker isolation required in production" />
        </div>
      </section>
    </div>
  );
}

function ConfigRow({ label, value }: { label: string; value: string }) {
  return <div className="rounded-md border border-hairline bg-surface px-4 py-3"><p className="font-mono text-[10px] uppercase tracking-[0.14em] text-mutedText">{label}</p><p className="mt-1 text-sm text-secondaryText">{value}</p></div>;
}
