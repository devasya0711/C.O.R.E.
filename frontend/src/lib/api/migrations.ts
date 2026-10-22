import { apiBaseUrl, apiRequest, ApiClientError } from "@/lib/api/client";
import type {
  MigrationAccepted,
  MigrationEvent,
  MigrationJobDetail,
  MigrationPrResponse,
} from "@/types/migrations";
import { streamSseWithReconnect } from "@/lib/api/sse";

export function startMigration(
  token: string,
  repositoryId: string,
  seamId: string,
  createGithubPr: boolean,
): Promise<MigrationAccepted> {
  return apiRequest<MigrationAccepted>(
    `/api/v1/repos/${encodeURIComponent(repositoryId)}/migrations`,
    token,
    {
      method: "POST",
      body: JSON.stringify({ seam_id: seamId, create_github_pr: createGithubPr }),
    },
  );
}

export function getMigrationJob(
  token: string,
  repositoryId: string,
  jobId: string,
): Promise<MigrationJobDetail> {
  return apiRequest<MigrationJobDetail>(
    `/api/v1/repos/${encodeURIComponent(repositoryId)}/migrations/${encodeURIComponent(jobId)}`,
    token,
  );
}

export function createMigrationPr(
  token: string,
  repositoryId: string,
  jobId: string,
  title: string,
): Promise<MigrationPrResponse> {
  return apiRequest<MigrationPrResponse>(
    `/api/v1/repos/${encodeURIComponent(repositoryId)}/migrations/${encodeURIComponent(jobId)}/pr`,
    token,
    { method: "POST", body: JSON.stringify({ job_id: jobId, title }) },
  );
}

export async function streamMigrationEvents(
  token: string,
  repositoryId: string,
  jobId: string,
  onEvent: (event: MigrationEvent) => void,
  signal: AbortSignal,
): Promise<void> {
  try {
    await streamSseWithReconnect(
      async (lastEventId) => {
        const headers = new Headers({
          Accept: "text/event-stream",
          Authorization: `Bearer ${token}`,
        });
        if (lastEventId) {
          headers.set("Last-Event-ID", lastEventId);
        }
        const response = await fetch(
          `${apiBaseUrl()}/api/v1/repos/${encodeURIComponent(repositoryId)}/migrations/${encodeURIComponent(jobId)}/stream`,
          { headers, cache: "no-store", signal },
        );
        if (!response.ok) {
          let message = `Migration stream failed with status ${response.status}.`;
          let code = "MIGRATION_STREAM_UNAVAILABLE";
          try {
            const body: unknown = await response.json();
            if (typeof body === "object" && body !== null && "error" in body) {
              const error = body.error;
              if (typeof error === "object" && error !== null) {
                const record = error as Record<string, unknown>;
                if (typeof record.message === "string") message = record.message;
                if (typeof record.code === "string") code = record.code;
              }
            }
          } catch {
            // Preserve the safe fallback message when the response is not JSON.
          }
          throw new ApiClientError(response.status, code, message);
        }
        return response;
      },
      onEvent,
      signal,
      { shouldReconnect: (error) => !(error instanceof ApiClientError) },
    );
  } catch (error: unknown) {
    if (error instanceof DOMException && error.name === "AbortError") return;
    if (error instanceof ApiClientError) throw error;
    throw new ApiClientError(
      502,
      "MIGRATION_STREAM_UNAVAILABLE",
      "Migration stream disconnected before completion.",
    );
  }
}
