"use client";

import Link from "next/link";
import { useAuth } from "@clerk/nextjs";
import { useCallback, useEffect, useMemo, useState } from "react";

import { ApiClientError } from "@/lib/api/client";
import { createMigrationPr, getMigrationJob, streamMigrationEvents } from "@/lib/api/migrations";
import { listRepositories } from "@/lib/api/repositories";
import type { MigrationEvent, MigrationJobDetail } from "@/types/migrations";
import { terminalMigrationEvents } from "@/types/migrations";

const steps = [
  { id: "static-analyzer", label: "Static Analyzer", events: [] },
  { id: "boundary-mapper", label: "Boundary Mapper", events: [] },
  { id: "impact-predictor", label: "Impact Predictor", events: [] },
  { id: "crag", label: "CRAG Evaluator", events: ["CRAG_DECISION"] },
  { id: "coder", label: "Refactor Coder", events: ["ARTIFACTS_READY"] },
  { id: "sandbox", label: "Pytest Sandbox", events: ["SANDBOX_ITERATION"] },
  { id: "github", label: "GitHub PR", events: ["PR_CREATED"] },
] as const;

type StepState = "pending" | "active" | "complete" | "failed";

function stepState(stepId: string, events: MigrationEvent[], detail: MigrationJobDetail | null): StepState {
  const failed = events.some((event) => event.event_type === "MIGRATION_FAILED" || event.event_type === "HUMAN_REVIEW");
  const hasPr = events.some((event) => event.event_type === "PR_CREATED") || Boolean(detail?.pr_url);
  const completed = detail?.status === "COMPLETED";
  if (["static-analyzer", "boundary-mapper", "impact-predictor"].includes(stepId)) {
    return detail ? "complete" : "active";
  }
  if (stepId === "context") return events.some((event) => event.event_type === "AGENT_START") || completed ? "complete" : "active";
  if (stepId === "crag") return events.some((event) => event.event_type === "CRAG_DECISION") || completed ? "complete" : events.length > 0 ? "active" : "pending";
  if (stepId === "coder") return events.some((event) => event.event_type === "ARTIFACTS_READY") || completed ? "complete" : failed ? "failed" : events.some((event) => event.event_type === "CRAG_DECISION") ? "active" : "pending";
  if (stepId === "sandbox") return events.some((event) => event.event_type === "SANDBOX_ITERATION" && event.payload.passed === true) || completed ? "complete" : failed ? "failed" : events.some((event) => event.event_type === "ARTIFACTS_READY") ? "active" : "pending";
  return hasPr ? "complete" : failed ? "failed" : completed ? "pending" : events.some((event) => event.event_type === "SANDBOX_ITERATION" && event.payload.passed === true) ? "active" : "pending";
}

export function MigrationOrchestrator({ repositoryId, jobId }: { repositoryId: string; jobId: string }) {
  const { getToken, isLoaded, isSignedIn } = useAuth();
  const [detail, setDetail] = useState<MigrationJobDetail | null>(null);
  const [events, setEvents] = useState<MigrationEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState<{ code: string; message: string } | null>(null);
  const [repositorySource, setRepositorySource] = useState<string | null>(null);
  const [prTitle, setPrTitle] = useState("");
  const [creatingPr, setCreatingPr] = useState(false);
  const [prError, setPrError] = useState<string | null>(null);
  const [selectedArtifact, setSelectedArtifact] = useState<string | null>(null);

  const refreshDetail = useCallback(async (token: string): Promise<MigrationJobDetail> => {
    const next = await getMigrationJob(token, repositoryId, jobId);
    setDetail(next);
    setSelectedArtifact((current) => current ?? Object.keys(next.generated_artifacts)[0] ?? null);
    return next;
  }, [jobId, repositoryId]);

  useEffect(() => {
    if (!isLoaded) return;
    if (!isSignedIn) {
      setLoading(false);
      setError({ code: "UNAUTHORIZED", message: "Sign in to inspect this migration." });
      return;
    }
    const controller = new AbortController();
    let active = true;
    async function load(): Promise<void> {
      setLoading(true);
      setError(null);
      try {
        const token = await getToken();
        if (!token) throw new ApiClientError(401, "UNAUTHORIZED", "Your session has expired. Sign in again.");
        await refreshDetail(token);
        try {
          const repositories = await listRepositories(token);
          setRepositorySource(repositories.find((repository) => repository.id === repositoryId)?.repo_url ?? null);
        } catch {
          setRepositorySource(null);
        }
        setStreaming(true);
        await streamMigrationEvents(token, repositoryId, jobId, (event) => {
          if (!active) return;
          setEvents((current) => current.some((item) => item.stream_id === event.stream_id && event.stream_id) ? current : [...current, event]);
          if (event.event_type === "ARTIFACTS_READY" || terminalMigrationEvents.has(event.event_type)) {
            void refreshDetail(token);
          }
        }, controller.signal);
      } catch (caught: unknown) {
        if (caught instanceof DOMException && caught.name === "AbortError") return;
        if (active) setError({ code: caught instanceof ApiClientError ? caught.code : "MIGRATION_LOAD_FAILED", message: caught instanceof Error ? caught.message : "Migration state could not be loaded." });
      } finally {
        if (active) {
          setLoading(false);
          setStreaming(false);
        }
      }
    }
    void load();
    return () => { active = false; controller.abort(); };
  }, [getToken, isLoaded, isSignedIn, jobId, refreshDetail, repositoryId]);

  const terminalEvent = [...events].reverse().find((event) => terminalMigrationEvents.has(event.event_type));
  const canCreatePr = detail?.status === "COMPLETED" && detail.sandbox_passed && !detail.pr_url && !detail.create_github_pr && repositorySource?.startsWith("https://github.com/");
  const artifactPaths = useMemo(() => Object.keys(detail?.generated_artifacts ?? {}).sort(), [detail]);
  const artifactContent = selectedArtifact && detail ? detail.generated_artifacts[selectedArtifact] : null;

  async function publishPr(): Promise<void> {
    setCreatingPr(true);
    setPrError(null);
    try {
      const token = await getToken();
      if (!token) throw new Error("Your session token is unavailable. Sign in again.");
      const response = await createMigrationPr(token, repositoryId, jobId, prTitle.trim() || "Extract service seam");
      setDetail((current) => current ? { ...current, pr_url: response.pr_url } : current);
    } catch (caught: unknown) {
      setPrError(caught instanceof Error ? caught.message : "GitHub PR creation failed.");
    } finally {
      setCreatingPr(false);
    }
  }

  if (loading) return <MigrationLoading />;
  if (error) return <MigrationMessage code={error.code} message={error.message} />;

  return (
    <div className="mx-auto max-w-7xl space-y-6">
      <header className="flex flex-col justify-between gap-4 border-b border-hairline pb-6 md:flex-row md:items-end">
        <div><Link href={`/repos/${repositoryId}/graph`} className="font-mono text-[10px] text-secondaryText hover:text-primaryText">← GRAPH STUDIO</Link><p className="mt-4 font-mono text-xs uppercase tracking-[0.2em] text-indigo-300">Migration orchestrator</p><h1 className="mt-2 text-2xl font-semibold">Execution-verified refactoring</h1><p className="mt-2 font-mono text-xs text-mutedText">JOB {jobId}</p></div>
        <div className="flex items-center gap-3">{streaming ? <span className="rounded-full border border-indigo-500/30 bg-indigo-500/10 px-3 py-1.5 font-mono text-[10px] text-indigo-300">LIVE SSE</span> : null}<StatusBadge status={terminalEvent?.event_type ?? detail?.status ?? "QUEUED"} /></div>
      </header>

      <section className="grid gap-4 lg:grid-cols-[1.25fr_0.75fr]">
        <div className="rounded-lg border border-hairline bg-panel p-6">
          <p className="font-mono text-[10px] uppercase tracking-[0.16em] text-secondaryText">Agent execution</p>
          <div className="mt-6 space-y-4">{steps.map((step) => <Step key={step.id} label={step.label} state={stepState(step.id, events, detail)} detail={step.id === "sandbox" && detail ? `${detail.retry_count} retries · ${detail.sandbox_isolation ?? "awaiting result"}` : undefined} />)}</div>
        </div>
        <div className="rounded-lg border border-hairline bg-panel p-6">
          <p className="font-mono text-[10px] uppercase tracking-[0.16em] text-secondaryText">Verification</p>
          <div className="mt-5 grid grid-cols-2 gap-4"><Metric label="CRAG" value={detail?.crag_status ?? "PENDING"} /><Metric label="Sandbox" value={detail?.sandbox_passed ? "PASSED" : "PENDING"} /><Metric label="Isolation" value={detail?.sandbox_isolation ?? "—"} /><Metric label="Retries" value={String(detail?.retry_count ?? 0)} /></div>
          {detail?.sandbox_isolation_note ? <p className="mt-5 text-xs leading-5 text-secondaryText">{detail.sandbox_isolation_note}</p> : null}
          {detail?.pr_url ? <a href={detail.pr_url} target="_blank" rel="noreferrer" className="mt-5 block rounded-md border border-emerald-500/30 bg-emerald-500/10 px-3 py-2.5 text-center text-sm font-semibold text-emerald-300 hover:bg-emerald-500/20">Open GitHub pull request</a> : null}
        </div>
      </section>

      {canCreatePr ? <section className="rounded-lg border border-indigo-500/30 bg-indigo-500/5 p-6"><p className="font-mono text-[10px] uppercase tracking-[0.16em] text-indigo-300">Human-approved publication</p><h2 className="mt-2 text-lg font-semibold">Create GitHub PR</h2><p className="mt-2 text-sm text-secondaryText">Sandbox verification passed. Review the generated artifacts below, then publish manually.</p><div className="mt-4 flex flex-col gap-3 sm:flex-row"><input value={prTitle} onChange={(event) => setPrTitle(event.target.value)} placeholder="Extract service seam" className="min-w-0 flex-1 rounded-md border border-hairline bg-surface px-3 py-2.5 text-sm text-primaryText placeholder:text-mutedText" /><button type="button" onClick={() => void publishPr()} disabled={creatingPr} className="rounded-md bg-indigo-600 px-4 py-2.5 text-sm font-semibold text-white hover:bg-indigo-500 disabled:cursor-wait disabled:opacity-50">{creatingPr ? "Publishing…" : "Create GitHub PR"}</button></div>{prError ? <p className="mt-3 text-xs text-red-300" role="alert">{prError}</p> : null}</section> : null}

      <section className="grid gap-4 lg:grid-cols-[0.8fr_1.2fr]">
        <EventLog events={events} />
        <ArtifactViewer paths={artifactPaths} selected={selectedArtifact} content={artifactContent} onSelect={setSelectedArtifact} logs={detail?.sandbox_logs ?? null} />
      </section>
    </div>
  );
}

function Step({ label, state, detail }: { label: string; state: StepState; detail?: string }) {
  const styles = { complete: "border-emerald-500/40 bg-emerald-500/10 text-emerald-300", active: "border-indigo-500/40 bg-indigo-500/10 text-indigo-300", failed: "border-red-500/40 bg-red-500/10 text-red-300", pending: "border-hairline bg-surface text-mutedText" };
  const marker = { complete: "✓", active: "•", failed: "!", pending: "—" };
  return <div className={`flex items-center gap-3 rounded-md border px-3 py-3 ${styles[state]}`}><span className="grid h-6 w-6 place-items-center rounded-full border font-mono text-xs">{marker[state]}</span><div><p className="text-sm font-medium">{label}</p>{detail ? <p className="mt-1 font-mono text-[10px] opacity-80">{detail}</p> : null}</div><span className="ml-auto font-mono text-[10px] uppercase">{state}</span></div>;
}

function StatusBadge({ status }: { status: string }) {
  const failed = status === "FAILED" || status === "MIGRATION_FAILED" || status === "HUMAN_REVIEW";
  return <span className={`rounded-full border px-3 py-1.5 font-mono text-[10px] ${failed ? "border-red-500/30 bg-red-500/10 text-red-300" : status === "COMPLETED" || status === "PR_CREATED" ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-300" : "border-amber-500/30 bg-amber-500/10 text-amber-300"}`}>{status}</span>;
}

function Metric({ label, value }: { label: string; value: string }) {
  return <div><p className="font-mono text-[10px] uppercase tracking-[0.14em] text-mutedText">{label}</p><p className="mt-1 font-mono text-xs text-primaryText">{value}</p></div>;
}

function EventLog({ events }: { events: MigrationEvent[] }) {
  return <section className="rounded-lg border border-hairline bg-panel p-6"><p className="font-mono text-[10px] uppercase tracking-[0.16em] text-secondaryText">Execution log</p><div className="mt-4 max-h-96 space-y-3 overflow-y-auto">{events.length === 0 ? <p className="text-sm text-secondaryText">Waiting for the first durable migration event…</p> : events.map((event, index) => <div key={`${event.stream_id ?? event.timestamp}-${index}`} className="rounded-md border border-hairline bg-surface p-3"><div className="flex items-center justify-between gap-3"><span className="font-mono text-xs text-indigo-300">{event.event_type}</span><time className="font-mono text-[10px] text-mutedText">{new Date(event.timestamp).toLocaleTimeString()}</time></div><p className="mt-2 font-mono text-[10px] leading-5 text-secondaryText">{Object.entries(event.payload).map(([key, value]) => `${key}: ${String(value)}`).join(" · ") || "No additional payload"}</p></div>)}</div></section>;
}

function ArtifactViewer({ paths, selected, content, onSelect, logs }: { paths: string[]; selected: string | null; content: string | null; onSelect: (path: string) => void; logs: string | null }) {
  return <section className="rounded-lg border border-hairline bg-panel p-6"><div className="flex flex-wrap items-center justify-between gap-3"><div><p className="font-mono text-[10px] uppercase tracking-[0.16em] text-secondaryText">Artifacts</p><h2 className="mt-2 text-lg font-semibold">Generated service files</h2></div><span className="font-mono text-[10px] text-mutedText">{paths.length} files</span></div>{paths.length === 0 ? <p className="mt-5 text-sm leading-6 text-secondaryText">Artifact metadata will appear when the refactor coder completes. The backend currently exposes generated files after durable persistence.</p> : <div className="mt-5 grid gap-4 md:grid-cols-[180px_1fr]"><nav aria-label="Generated artifacts" className="space-y-1">{paths.map((path) => <button key={path} type="button" onClick={() => onSelect(path)} className={`block w-full truncate rounded-md px-3 py-2 text-left font-mono text-[10px] ${selected === path ? "bg-indigo-500/15 text-indigo-200" : "text-secondaryText hover:bg-surface hover:text-primaryText"}`}>{path}</button>)}</nav><pre className="max-h-[520px] min-h-64 overflow-auto rounded-md border border-hairline bg-surface p-4 font-mono text-xs leading-6 text-secondaryText"><code>{content ?? "Select an artifact"}</code></pre></div>}{logs ? <details className="mt-5 border-t border-hairline pt-4"><summary className="cursor-pointer font-mono text-[10px] uppercase tracking-[0.14em] text-mutedText">Sandbox diagnostics</summary><pre className="mt-3 max-h-56 overflow-auto whitespace-pre-wrap rounded-md bg-surface p-3 font-mono text-[10px] leading-5 text-secondaryText">{logs}</pre></details> : null}</section>;
}

function MigrationLoading() {
  return <div className="mx-auto max-w-7xl animate-pulse space-y-6"><div className="h-8 w-80 rounded bg-surface" /><div className="grid gap-4 lg:grid-cols-2"><div className="h-72 rounded-lg border border-hairline bg-panel" /><div className="h-72 rounded-lg border border-hairline bg-panel" /></div></div>;
}

function MigrationMessage({ code, message }: { code: string; message: string }) {
  return <section className="mx-auto max-w-lg rounded-lg border border-red-500/30 bg-panel p-6"><p className="font-mono text-xs uppercase tracking-[0.16em] text-red-300">{code}</p><h1 className="mt-3 text-xl font-semibold">Migration unavailable</h1><p className="mt-3 text-sm leading-6 text-secondaryText">{message}</p><Link href="/dashboard" className="mt-5 inline-flex rounded-md bg-indigo-600 px-4 py-2.5 text-sm font-semibold text-white hover:bg-indigo-500">Back to repositories</Link></section>;
}
