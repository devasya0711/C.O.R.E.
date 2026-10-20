import Link from "next/link";

import type { RepositorySummary } from "@/types/repositories";

interface RepositoryCardProps {
  repository: RepositorySummary;
  retrying: boolean;
  onRetry: (repository: RepositorySummary) => void;
}

const activeStatuses = new Set(["PENDING", "INGESTING"]);

function statusClass(status: string): string {
  if (status === "READY") {
    return "border-emerald-500/30 bg-emerald-500/10 text-emerald-300";
  }
  if (status === "FAILED") {
    return "border-red-500/30 bg-red-500/10 text-red-300";
  }
  return "border-amber-500/30 bg-amber-500/10 text-amber-300";
}

function formatDate(value: string | null): string {
  if (!value) {
    return "No ingestion yet";
  }
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) {
    return "Unknown update time";
  }
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

export function RepositoryCard({ repository, retrying, onRetry }: RepositoryCardProps) {
  const ingestion = repository.ingestion;
  const active = activeStatuses.has(repository.status);
  const progress = Math.max(0, Math.min(100, ingestion?.progress ?? 0));

  return (
    <article className="flex min-h-56 flex-col rounded-lg border border-hairline bg-panel p-5 transition hover:border-indigo-500/50">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <p className="truncate font-mono text-[10px] uppercase tracking-[0.16em] text-mutedText">
            Repository
          </p>
          <h2 className="mt-2 truncate text-base font-semibold" title={repository.name}>
            {repository.name}
          </h2>
        </div>
        <span className={`shrink-0 rounded-full border px-2 py-1 font-mono text-[10px] ${statusClass(repository.status)}`}>
          {repository.status}
        </span>
      </div>

      <p className="mt-3 truncate font-mono text-xs text-secondaryText" title={repository.repo_url}>
        {repository.repo_url}
      </p>

      <div className="mt-5 grid grid-cols-2 gap-3 border-y border-hairline py-4">
        <Metric label="Nodes" value={repository.node_count.toLocaleString()} />
        <Metric label="Edges" value={repository.edge_count.toLocaleString()} />
      </div>

      <div className="mt-4 flex-1">
        <div className="flex items-center justify-between gap-3 text-xs">
          <span className="font-mono text-secondaryText">
            {ingestion?.stage ?? (active ? "QUEUED" : "No ingestion run")}
          </span>
          {ingestion ? <span className="font-mono text-mutedText">{progress}%</span> : null}
        </div>
        {ingestion ? (
          <div
            className="mt-2 h-1.5 overflow-hidden rounded-full bg-surface"
            role="progressbar"
            aria-label={`${repository.name} ingestion progress`}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={progress}
          >
            <div
              className={`h-full rounded-full transition-[width] duration-300 ${
                repository.status === "FAILED" ? "bg-red-500" : "bg-indigo-500"
              }`}
              style={{ width: `${progress}%` }}
            />
          </div>
        ) : null}
        {repository.status === "FAILED" && ingestion?.error ? (
          <p className="mt-2 line-clamp-2 text-xs leading-5 text-red-300">{ingestion.error}</p>
        ) : null}
      </div>

      <div className="mt-4 flex items-center justify-between gap-3 border-t border-hairline pt-4">
        <span className="font-mono text-[10px] text-mutedText">
          Updated {formatDate(ingestion?.updated_at ?? null)}
        </span>
        {repository.status === "FAILED" ? (
          <button
            type="button"
            onClick={() => onRetry(repository)}
            disabled={retrying}
            className="rounded-md border border-red-500/40 px-3 py-1.5 text-xs font-medium text-red-300 hover:bg-red-500/10 disabled:cursor-wait disabled:opacity-50"
          >
            {retrying ? "Retrying…" : "Retry ingestion"}
          </button>
        ) : repository.status === "READY" ? (
          <Link
            href={`/repos/${repository.id}/graph`}
            className="rounded-md border border-emerald-500/30 px-3 py-1.5 text-xs font-medium text-emerald-300 hover:bg-emerald-500/10"
          >
            Open graph
          </Link>
        ) : null}
      </div>
    </article>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="font-mono text-[10px] uppercase tracking-[0.14em] text-mutedText">{label}</p>
      <p className="mt-1 font-mono text-sm font-semibold text-primaryText">{value}</p>
    </div>
  );
}
