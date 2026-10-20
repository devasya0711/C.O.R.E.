"use client";

import { useAuth } from "@clerk/nextjs";
import { useCallback, useEffect, useMemo, useState } from "react";

import { ApiClientError } from "@/lib/api/client";
import { createRepository, listRepositories } from "@/lib/api/repositories";
import type {
  RepositoryCreatePayload,
  RepositorySummary,
} from "@/types/repositories";

import { ImportRepositoryDialog } from "./import-repository-dialog";
import { IngestionStatus } from "./ingestion-status";
import { RepositoryCard } from "./repository-card";

const sampleRepository: RepositoryCreatePayload = {
  name: "Sample E-Commerce Monolith",
  repo_url: "local://sample_monolith",
  default_branch: "main",
  use_sample_monolith: true,
};

function activeIngestion(repository: RepositorySummary): boolean {
  return repository.status === "PENDING" || repository.status === "INGESTING";
}

export function RepositoryDashboard() {
  const { getToken, isLoaded, isSignedIn } = useAuth();
  const [repositories, setRepositories] = useState<RepositorySummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [retryingId, setRetryingId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [errorCode, setErrorCode] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const loadRepositories = useCallback(
    async (silent = false): Promise<void> => {
      if (!isLoaded || !isSignedIn) {
        return;
      }
      if (silent) {
        setRefreshing(true);
      } else {
        setLoading(true);
      }
      try {
        const token = await getToken();
        if (!token) {
          throw new Error("Your session token is unavailable. Sign in again.");
        }
        const nextRepositories = await listRepositories(token);
        setRepositories(nextRepositories);
        setError(null);
        setErrorCode(null);
      } catch (caught: unknown) {
        if (caught instanceof DOMException && caught.name === "AbortError") {
          return;
        }
        setError(caught instanceof Error ? caught.message : "Repositories could not be loaded.");
        setErrorCode(caught instanceof ApiClientError ? caught.code : null);
      } finally {
        if (silent) {
          setRefreshing(false);
        } else {
          setLoading(false);
        }
      }
    },
    [getToken, isLoaded, isSignedIn],
  );

  useEffect(() => {
    void loadRepositories();
  }, [loadRepositories]);

  const hasActiveIngestion = useMemo(
    () => repositories.some(activeIngestion),
    [repositories],
  );

  useEffect(() => {
    if (!hasActiveIngestion || !isLoaded || !isSignedIn) {
      return;
    }
    const interval = window.setInterval(() => {
      void loadRepositories(true);
    }, 4000);
    return () => window.clearInterval(interval);
  }, [hasActiveIngestion, isLoaded, isSignedIn, loadRepositories]);

  async function submitImport(payload: RepositoryCreatePayload): Promise<void> {
    setSubmitting(true);
    setError(null);
    setErrorCode(null);
    setNotice(null);
    try {
      const token = await getToken();
      if (!token) {
        throw new Error("Your session token is unavailable. Sign in again.");
      }
      const accepted = await createRepository(token, payload);
      setNotice(`Ingestion queued for ${payload.name}. Job ${accepted.repo_id.slice(0, 8)} is starting.`);
      setDialogOpen(false);
      await loadRepositories(true);
    } catch (caught: unknown) {
      setError(caught instanceof Error ? caught.message : "Repository ingestion could not start.");
      setErrorCode(caught instanceof ApiClientError ? caught.code : null);
    } finally {
      setSubmitting(false);
    }
  }

  async function retryImport(repository: RepositorySummary): Promise<void> {
    setRetryingId(repository.id);
    setError(null);
    setErrorCode(null);
    try {
      const token = await getToken();
      if (!token) {
        throw new Error("Your session token is unavailable. Sign in again.");
      }
      await createRepository(token, {
        name: repository.name,
        repo_url: repository.repo_url,
        default_branch: repository.default_branch,
        use_sample_monolith: repository.repo_url === "local://sample_monolith",
      });
      setNotice(`Retry queued for ${repository.name}.`);
      await loadRepositories(true);
    } catch (caught: unknown) {
      setError(caught instanceof Error ? caught.message : "Repository retry could not start.");
      setErrorCode(caught instanceof ApiClientError ? caught.code : null);
    } finally {
      setRetryingId(null);
    }
  }

  const showInitialLoading = loading && repositories.length === 0 && !error;

  return (
    <div className="mx-auto max-w-7xl space-y-8">
      <section className="flex flex-col justify-between gap-5 border-b border-hairline pb-6 md:flex-row md:items-end">
        <div>
          <p className="font-mono text-xs uppercase tracking-[0.2em] text-indigo-300">Workspace / 001</p>
          <h1 className="mt-3 text-2xl font-semibold tracking-[-0.015em]">Repositories</h1>
          <p className="mt-2 max-w-2xl text-sm leading-6 text-secondaryText">
            Register a codebase, watch ingestion move from source checkout to graph indexing, and open the analysis studio when it is ready.
          </p>
        </div>
        <div className="flex flex-wrap gap-3">
          <button
            type="button"
            onClick={() => setDialogOpen(true)}
            className="rounded-md bg-indigo-600 px-4 py-2.5 text-sm font-semibold text-white hover:bg-indigo-500"
          >
            Import GitHub Repo
          </button>
          <button
            type="button"
            onClick={() => void submitImport(sampleRepository)}
            disabled={submitting}
            className="rounded-md border border-hairline bg-panel px-4 py-2.5 text-sm font-medium text-secondaryText hover:bg-surface hover:text-primaryText disabled:cursor-wait disabled:opacity-50"
          >
            Load Sample E-Commerce Monolith
          </button>
        </div>
      </section>

      <IngestionStatus active={hasActiveIngestion} />

      {notice ? (
        <div className="flex items-start justify-between gap-4 rounded-md border border-emerald-500/30 bg-emerald-500/10 px-4 py-3 text-sm text-emerald-200" role="status" aria-live="polite">
          <span>{notice}</span>
          <button
            type="button"
            onClick={() => setNotice(null)}
            aria-label="Dismiss notification"
            className="text-emerald-300 hover:text-emerald-100"
          >
            ×
          </button>
        </div>
      ) : null}

      {error ? (
        <ErrorCard code={errorCode} message={error} onRetry={() => void loadRepositories()} />
      ) : null}

      {showInitialLoading ? <RepositorySkeletons /> : null}

      {!loading && repositories.length === 0 && !error ? (
        <EmptyState
          onImport={() => setDialogOpen(true)}
          onSample={() => void submitImport(sampleRepository)}
          pending={submitting}
        />
      ) : null}

      {repositories.length > 0 ? (
        <section aria-labelledby="repository-list-title">
          <div className="mb-4 flex items-center justify-between gap-4">
            <div>
              <h2 id="repository-list-title" className="text-lg font-semibold">Tracked codebases</h2>
              <p className="mt-1 font-mono text-xs text-mutedText">
                {repositories.length} {repositories.length === 1 ? "repository" : "repositories"} · {refreshing ? "refreshing" : "polling while active"}
              </p>
            </div>
            <button
              type="button"
              onClick={() => void loadRepositories(true)}
              disabled={refreshing}
              className="rounded-md border border-hairline px-3 py-2 text-xs font-medium text-secondaryText hover:bg-surface hover:text-primaryText disabled:cursor-wait disabled:opacity-50"
            >
              {refreshing ? "Refreshing…" : "Refresh"}
            </button>
          </div>
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            {repositories.map((repository) => (
              <RepositoryCard
                key={repository.id}
                repository={repository}
                retrying={retryingId === repository.id}
                onRetry={(item) => void retryImport(item)}
              />
            ))}
          </div>
        </section>
      ) : null}

      <ImportRepositoryDialog
        open={dialogOpen}
        pending={submitting}
        onClose={() => setDialogOpen(false)}
        onSubmit={submitImport}
      />
    </div>
  );
}

function EmptyState({
  onImport,
  onSample,
  pending,
}: {
  onImport: () => void;
  onSample: () => void;
  pending: boolean;
}) {
  return (
    <section className="relative overflow-hidden rounded-lg border border-dashed border-hairline bg-panel px-6 py-16 text-center">
      <div className="pointer-events-none absolute inset-0 opacity-40 [background-image:radial-gradient(#262938_1px,transparent_1px)] [background-size:18px_18px]" />
      <div className="relative mx-auto max-w-xl">
        <p className="font-mono text-xs uppercase tracking-[0.18em] text-indigo-300">No repositories indexed</p>
        <h2 className="mt-3 text-xl font-semibold">Start with a codebase boundary.</h2>
        <p className="mt-3 text-sm leading-6 text-secondaryText">
          Import a GitHub repository or load the deterministic sample monolith to begin AST parsing and graph indexing.
        </p>
        <div className="mt-7 flex flex-wrap justify-center gap-3">
          <button type="button" onClick={onImport} className="rounded-md bg-indigo-600 px-4 py-2.5 text-sm font-semibold text-white hover:bg-indigo-500">
            Import GitHub Repo
          </button>
          <button type="button" onClick={onSample} disabled={pending} className="rounded-md border border-hairline bg-surface px-4 py-2.5 text-sm font-medium text-secondaryText hover:text-primaryText disabled:cursor-wait disabled:opacity-50">
            Load Sample E-Commerce Monolith
          </button>
        </div>
      </div>
    </section>
  );
}

function ErrorCard({ code, message, onRetry }: { code: string | null; message: string; onRetry: () => void }) {
  return (
    <section className="flex flex-col justify-between gap-4 rounded-md border border-red-500/30 bg-red-500/10 p-4 md:flex-row md:items-center" role="alert">
      <div>
        <p className="font-mono text-[10px] uppercase tracking-[0.16em] text-red-300">{code ?? "API_ERROR"}</p>
        <p className="mt-1 text-sm text-red-100">{message}</p>
      </div>
      <button type="button" onClick={onRetry} className="shrink-0 rounded-md border border-red-500/40 px-3 py-2 text-xs font-medium text-red-200 hover:bg-red-500/10">
        Retry request
      </button>
    </section>
  );
}

function RepositorySkeletons() {
  return (
    <section aria-label="Loading repositories" className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
      {["one", "two", "three"].map((key) => (
        <div key={key} className="h-56 animate-pulse rounded-lg border border-hairline bg-panel" />
      ))}
    </section>
  );
}
