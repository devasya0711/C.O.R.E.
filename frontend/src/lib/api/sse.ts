import { terminalMigrationEvents } from "../../types/migrations";
import type { MigrationEvent, MigrationEventType } from "@/types/migrations";

const migrationEventTypes: ReadonlySet<string> = new Set([
  "AGENT_START",
  "CRAG_DECISION",
  "SANDBOX_ITERATION",
  "ARTIFACTS_READY",
  "PR_CREATED",
  "MIGRATION_FAILED",
  "HUMAN_REVIEW",
  "COMPLETED",
]);

export interface ParsedSseBlock {
  id?: string;
  event?: string;
  data?: string;
}

export type SseRequest = (lastEventId: string | undefined) => Promise<Response>;

export interface SseReconnectOptions {
  maxReconnects?: number;
  reconnectDelayMs?: number;
  shouldReconnect?: (error: unknown) => boolean;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

export function parseSseBlock(block: string): ParsedSseBlock | null {
  if (!block.trim()) return null;
  const result: ParsedSseBlock = {};
  const dataLines: string[] = [];
  for (const line of block.replaceAll("\r\n", "\n").split("\n")) {
    if (line.startsWith("id:")) {
      result.id = line.slice(3).trim();
    } else if (line.startsWith("event:")) {
      result.event = line.slice(6).trim();
    } else if (line.startsWith("data:")) {
      dataLines.push(line.slice(5).trim());
    }
  }
  if (dataLines.length > 0) result.data = dataLines.join("\n");
  return result;
}

export function parseMigrationEvent(parsed: ParsedSseBlock): MigrationEvent | null {
  if (!parsed.data) return null;
  try {
    const payload: unknown = JSON.parse(parsed.data);
    if (!isRecord(payload)) return null;
    const jobId = payload.job_id;
    const repoId = payload.repo_id;
    const eventType = payload.event_type;
    if (
      typeof jobId !== "string" ||
      typeof repoId !== "string" ||
      typeof eventType !== "string" ||
      !migrationEventTypes.has(eventType)
    ) {
      return null;
    }
    const rawPayload = payload.payload;
    return {
      job_id: jobId,
      repo_id: repoId,
      event_type: eventType as MigrationEventType,
      payload: isRecord(rawPayload) ? rawPayload : {},
      timestamp: typeof payload.timestamp === "string" ? payload.timestamp : new Date().toISOString(),
      stream_id: parsed.id,
    };
  } catch {
    return null;
  }
}

export async function consumeSseResponse(
  response: Response,
  onEvent: (event: MigrationEvent) => void,
): Promise<MigrationEventType | null> {
  if (!response.body) {
    throw new Error("Migration stream returned no body.");
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8", { fatal: true });
  let buffer = "";
  let terminal: MigrationEventType | null = null;

  const consumeBlock = (block: string): boolean => {
    const parsed = parseSseBlock(block);
    if (!parsed?.data) return false;
    const event = parseMigrationEvent(parsed);
    if (!event) return false;
    onEvent(event);
    if (terminalMigrationEvents.has(event.event_type)) {
      terminal = event.event_type;
      return true;
    }
    return false;
  };

  try {
    while (true) {
      const chunk = await reader.read();
      if (chunk.done) {
        buffer += decoder.decode();
        if (buffer.trim()) consumeBlock(buffer);
        break;
      }
      if (!chunk.value) continue;
      buffer += decoder.decode(chunk.value, { stream: true }).replaceAll("\r\n", "\n");
      const blocks = buffer.split("\n\n");
      buffer = blocks.pop() ?? "";
      for (const block of blocks) {
        if (consumeBlock(block)) return terminal;
      }
    }
  } finally {
    reader.releaseLock();
  }
  return terminal;
}

function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

async function waitForReconnect(signal: AbortSignal, delayMs: number): Promise<void> {
  await new Promise<void>((resolve, reject) => {
    if (signal.aborted) {
      reject(new DOMException("Aborted", "AbortError"));
      return;
    }
    const timer: { id?: ReturnType<typeof globalThis.setTimeout> } = {};
    const onAbort = (): void => {
      if (timer.id !== undefined) globalThis.clearTimeout(timer.id);
      reject(new DOMException("Aborted", "AbortError"));
    };
    timer.id = globalThis.setTimeout(() => {
      signal.removeEventListener("abort", onAbort);
      resolve();
    }, delayMs);
    signal.addEventListener("abort", onAbort, { once: true });
  });
}

export async function streamSseWithReconnect(
  request: SseRequest,
  onEvent: (event: MigrationEvent) => void,
  signal: AbortSignal,
  options: SseReconnectOptions = {},
): Promise<void> {
  const maxReconnects = options.maxReconnects ?? 8;
  const reconnectDelayMs = options.reconnectDelayMs ?? 500;
  let lastEventId: string | undefined;
  let reconnects = 0;

  while (!signal.aborted && reconnects <= maxReconnects) {
    try {
      const terminal = await consumeSseResponse(
        await request(lastEventId),
        (event) => {
          if (event.stream_id) lastEventId = event.stream_id;
          onEvent(event);
        },
      );
      if (terminal || signal.aborted) return;
    } catch (error: unknown) {
      if (signal.aborted || isAbortError(error)) return;
      if (options.shouldReconnect && !options.shouldReconnect(error)) {
        throw error;
      }
      if (reconnects >= maxReconnects) throw error;
    }
    reconnects += 1;
    if (reconnects > maxReconnects || signal.aborted) return;
    await waitForReconnect(signal, reconnectDelayMs);
  }
}
