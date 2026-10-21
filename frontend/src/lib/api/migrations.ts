import { apiBaseUrl, apiRequest, ApiClientError } from "@/lib/api/client";
import type {
  MigrationAccepted,
  MigrationEvent,
  MigrationJobDetail,
  MigrationPrResponse,
  MigrationEventType,
} from "@/types/migrations";
import { terminalMigrationEvents } from "@/types/migrations";
import { parseMigrationEvent, parseSseBlock } from "@/lib/api/sse";

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

async function consumeStream(
  response: Response,
  onEvent: (event: MigrationEvent) => void,
): Promise<MigrationEventType | null> {
  if (!response.body) {
    throw new ApiClientError(502, "MIGRATION_STREAM_UNAVAILABLE", "Migration stream returned no body.");
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let terminal: MigrationEventType | null = null;
  try {
    while (true) {
      const chunk = await reader.read();
      if (chunk.done) {
        break;
      }
      buffer += decoder.decode(chunk.value, { stream: true }).replaceAll("\r\n", "\n");
      const blocks = buffer.split("\n\n");
      buffer = blocks.pop() ?? "";
      for (const block of blocks) {
        const parsed = parseSseBlock(block);
        if (!parsed?.data) {
          continue;
        }
        const event = parseMigrationEvent(parsed);
        if (!event) {
          continue;
        }
        onEvent(event);
        if (terminalMigrationEvents.has(event.event_type)) {
          terminal = event.event_type;
          return terminal;
        }
      }
    }
  } finally {
    reader.releaseLock();
  }
  return terminal;
}

export async function streamMigrationEvents(
  token: string,
  repositoryId: string,
  jobId: string,
  onEvent: (event: MigrationEvent) => void,
  signal: AbortSignal,
): Promise<void> {
  let lastEventId: string | undefined;
  let reconnects = 0;
  while (!signal.aborted && reconnects <= 8) {
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
    const terminal = await consumeStream(response, (event) => {
      if (event.stream_id) lastEventId = event.stream_id;
      onEvent(event);
    });
    if (terminal || signal.aborted) return;
    reconnects += 1;
    await new Promise<void>((resolve, reject) => {
      const timeout = window.setTimeout(resolve, 500);
      signal.addEventListener("abort", () => {
        window.clearTimeout(timeout);
        reject(new DOMException("Aborted", "AbortError"));
      }, { once: true });
    });
  }
}
